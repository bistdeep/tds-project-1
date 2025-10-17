# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "fastapi[standard]",
#   "uvicorn",
#   "requests",
#   "python-dotenv"
# ]
# ///

import os
import base64
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, HTTPException, BackgroundTasks

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
AIPIPE_API_KEY = os.getenv("AIPIPE_API_KEY")
SECRET = os.getenv("SECRET")


def validate_secret(secret: str) -> bool:
    return secret == SECRET


def create_github_repo(repo_name: str):
    payload = {"name": repo_name, "private": False, "auto_init": False, "license_template": "mit"}
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    response = requests.post("https://api.github.com/user/repos", headers=headers, json=payload)
    if response.status_code != 201:
        raise Exception(f"Failed to create repo: {response.status_code}, {response.text}")
    return response.json()


def enable_github_pages(repo_name: str):
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    payload = {"build_type": "legacy", "source": {"branch": "main", "path": "/"}}
    response = requests.post(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/pages", headers=headers, json=payload
    )
    if response.status_code not in (201, 202):
        raise Exception(f"Failed to enable GitHub Pages: {response.status_code}, {response.text}")


def get_sha_of_latest_commit(repo_name: str, branch: str = "main") -> str:
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    response = requests.get(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/commits/{branch}", headers=headers
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get latest commit sha: {response.status_code}, {response.text}")
    return response.json().get("sha")


def repository_exists(repo_name: str) -> bool:
    """Check if a repository exists under the current user's account"""
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    response = requests.get(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}", headers=headers
    )
    return response.status_code == 200


def check_repo_has_required_files(repo_name: str) -> bool:
    """Check if a repository has the required files (index.html and README.md)"""
    required_files = ["index.html", "README.md"]
    
    for file_path in required_files:
        content, _ = get_file_content_from_repo(repo_name, file_path)
        if not content:
            # If any required file is missing, return False
            return False
    
    # All required files exist
    return True


def get_file_content_from_repo(repo_name: str, file_path: str, branch: str = "main"):
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    response = requests.get(
        f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/contents/{file_path}?ref={branch}", headers=headers
    )
    if response.status_code == 200:
        data = response.json()
        content = base64.b64decode(data.get("content", "")).decode("utf-8") if data.get("content") else ""
        return content, data.get("sha")
    return "", None


def push_files_to_repo(repo_name: str, files: list[dict], round: int):
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    for file in files:
        file_name = file.get("name")
        file_content = file.get("content")
        file_sha = file.get("sha")
        file_action = file.get("action", "create" if round == 1 else "update")
        
        print(f"Processing file {file_name} with action {file_action}, SHA: {file_sha[:7] if file_sha else 'None'}")
        
        # For updates in round 2, we must have a SHA
        if file_action == "update" and not file_sha:
            print(f"Missing SHA for {file_name} - attempting to retrieve it...")
            current_content, current_sha = get_file_content_from_repo(repo_name, file_name)
            if current_sha:
                file_sha = current_sha
                print(f"Retrieved SHA: {file_sha[:7]}")
            else:
                print(f"WARNING: Unable to get SHA for file {file_name}. Falling back to create action.")
                file_action = "create"  # Fall back to create if we can't get the SHA
        
        if isinstance(file_content, bytes):
            payload_content = base64.b64encode(file_content).decode("utf-8")
        else:
            payload_content = base64.b64encode(file_content.encode("utf-8")).decode("utf-8")

        payload = {
            "message": f"{'Update' if file_action == 'update' else 'Add'} {file_name} - Round {round}",
            "content": payload_content,
        }
        
        # Include SHA in payload for updates (required by GitHub API)
        if file_action == "update" and file_sha:
            payload["sha"] = file_sha
            print(f"Including SHA {file_sha[:7]} in update payload for {file_name}")

        print(f"Pushing {file_action} for {file_name} to GitHub...")
        response = requests.put(
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/contents/{file_name}", 
            headers=headers, 
            json=payload
        )
        
        if response.status_code not in [200, 201]:
            print(f"Error response from GitHub: {response.status_code}, {response.text}")
            raise Exception(f"Failed to push file {file_name}: {response.status_code}, {response.text}")
        else:
            print(f"Successfully {file_action}d {file_name}")
            
            # If this was initially going to be an update but we had to create it,
            # let's update the file dictionary with the new SHA for future operations
            if file_action == "create" and "sha" not in file:
                new_sha = response.json().get("content", {}).get("sha")
                if new_sha:
                    file["sha"] = new_sha
                    print(f"Updated SHA for {file_name} to {new_sha[:7]}")


def call_llm(prompt: str) -> str:
    headers = {"Authorization": f"Bearer {AIPIPE_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 4000, "temperature": 0.0}
    response = requests.post("https://aipipe.org/openai/v1/chat/completions", headers=headers, json=payload)
    if response.status_code != 200:
        raise Exception(f"LLM API call failed: {response.status_code}, {response.text}")
    return response.json()["choices"][0]["message"]["content"]


def write_code_with_llm(brief: str, checks: list, attachments: list):
    attachment_context = ""
    for attachment in attachments or []:
        name = attachment.get("name")
        data_uri = attachment.get("url")

        if not (isinstance(data_uri, str) and data_uri.startswith("data:")):
            continue # Skip if not a valid data URI

        attachment_context += f"\n\nAttachment {name}:\n"
        
        try:
            # Try to decode as text (for JSON, CSV, etc.)
            header, data = data_uri.split(",", 1)
            content_bytes = base64.b64decode(data)
            content_text = content_bytes.decode('utf-8')
            attachment_context += content_text
        except Exception:
            # If it fails (it's binary like an image),
            # pass the ENTIRE data URI to the LLM.
            attachment_context += f"[Binary content - use this data URI directly in HTML: {data_uri}]"   
    prompt = f"""
You are an assistant that generates a static web application deployable to GitHub Pages.

Task brief:
{brief}

Evaluation checks:
{chr(10).join(f'- {c}' for c in (checks or []))}

Attachments context:
{attachment_context or 'None'}

Requirements:
- Produce exactly two files: `index.html` and `README.md`.
- `index.html` must be a single, self-contained HTML file with all CSS and JS inlined.
  Embed images or binary assets as data URIs when needed.
- `README.md` must contain: Overview, Features, Setup Instructions, Usage, Code Explanation, License.
- No server-side code; the site must be deployable to GitHub Pages.
- Be responsive and professional.

Output format (REQUIRED):
Return ONLY a JSON document that matches the exact shape below. Do not add any text outside the JSON.

Example JSON (use this exact structure):
{{
  "files": [
    {{
      "name": "index.html",
      "content": "<!DOCTYPE html>...your full inline html/css/js..."
    }},
    {{
      "name": "README.md",
      "content": "# Project Title\n\nOverview..."
    }}
  ]
}}
"""

    llm_response = call_llm(prompt)


    # strict JSON parse
    response_data = json.loads(llm_response)
    files = response_data.get("files", [])
    filtered = []
    for item in files:
        if item.get("name") in ("index.html", "README.md"):
            filtered.append({"name": item.get("name"), "content": item.get("content")})
    return filtered


def update_code_with_llm(brief: str, checks: list, attachments: list, repo_name: str, existing_files: list):
    #
    # --- START OF FIX ---
    #
    attachment_context = ""
    for attachment in attachments or []:
        name = attachment.get("name")
        data_uri = attachment.get("url")

        if not (isinstance(data_uri, str) and data_uri.startswith("data:")):
            continue

        attachment_context += f"\n\nAttachment {name}:\n"
        
        try:
            # Try to decode as text
            header, data = data_uri.split(",", 1)
            content_bytes = base64.b64decode(data)
            content_text = content_bytes.decode('utf-8')
            attachment_context += content_text
        except Exception:
            # Pass the data URI directly
            attachment_context += f"[Binary content - use this data URI directly in HTML: {data_uri}]"
    #
    # --- END OF FIX ---
    #

    existing_context = ""
    for ef in existing_files or []:
        existing_context += f"\n\nExisting {ef['name']}:\n{ef.get('content', '')}"

    prompt = f"""
You are tasked with updating an existing web application.

New Requirements:
{brief}

Evaluation Checks:
{chr(10).join(f'- {c}' for c in (checks or []))}

New Attachments:
{attachment_context or 'None'}

Existing Files:
{existing_context or 'None'}

Please update the application to meet the new requirements while maintaining existing functionality where appropriate.

Output format for updates (REQUIRED):
Return ONLY a JSON document with the exact structure below. Do not include any other text.

Example JSON for updates:
{{
  "files": [
    {{
      "name": "index.html",
      "content": "<!DOCTYPE html>...updated inline html/css/js...",
      "action": "update"
    }},
    {{
      "name": "README.md",
      "content": "# Updated Project\n\n...",
      "action": "update"
    }}
  ]
}}
"""

    llm_response = call_llm(prompt)
    response_data = json.loads(llm_response)
    files = response_data.get("files", [])
    filtered = []
    
    # Create a mapping of file names to their SHA values
    sha_map = {ef['name']: ef.get('sha') for ef in existing_files if 'name' in ef and 'sha' in ef}
    
    for f in files:
        if f.get("name") in ("index.html", "README.md"):
            entry = {
                "name": f.get("name"),
                "content": f.get("content"),
                "action": f.get("action", "update")  # Default to "update" for round2
            }
            
            # Add the SHA value if this is an update and we have the SHA
            if entry["action"] == "update" and entry["name"] in sha_map:
                entry["sha"] = sha_map[entry["name"]]
                print(f"Adding SHA {sha_map[entry['name']]} to file {entry['name']}")
            
            filtered.append(entry)
    
    print(f"Prepared {len(filtered)} files for update")
    return filtered

def ping_evaluation_server(evaluation_url: str, payload: dict, max_retries: int = 3):
    headers = {"Content-Type": "application/json"}
    for attempt in range(max_retries):
        try:
            r = requests.post(evaluation_url, json=payload, headers=headers, timeout=10)
            if r.status_code in (200, 201, 202):
                return True
        except Exception:
            pass
        time.sleep(2 ** attempt)
    return False


def build_initial_application(data):
    try:
        repo_name = data["task"]
        files = write_code_with_llm(data["brief"], data.get("checks", []), data.get("attachments", []))
        create_github_repo(repo_name)
        push_files_to_repo(repo_name, files, 1)
        enable_github_pages(repo_name)
        commit_sha = get_sha_of_latest_commit(repo_name)
        evaluation_payload = {
            "email": data["email"],
            "task": data["task"],
            "round": data["round"],
            "nonce": data["nonce"],
            "repo_url": f"https://github.com/{GITHUB_USERNAME}/{repo_name}",
            "commit_sha": commit_sha,
            "pages_url": f"https://{GITHUB_USERNAME}.github.io/{repo_name}/",
        }
        ping_evaluation_server(data["evaluation_url"], evaluation_payload)
        return {"status": "success", "repo_url": evaluation_payload["repo_url"]}
    except Exception as e:
        print(f"Initial application build failed: {e}")
        raise e


def revise_existing_application(data):
    try:
        repo_name = data["task"]
        existing_files = []
        key_files = ["index.html", "README.md"]
        
        # Fetch existing files with their SHA values
        print(f"Fetching existing files for repo: {repo_name}")
        for file_name in key_files:
            content, sha = get_file_content_from_repo(repo_name, file_name)
            if content:
                existing_files.append({"name": file_name, "content": content, "sha": sha})
                print(f"Found existing file: {file_name} with SHA: {sha[:7] if sha else 'None'}")
            else:
                print(f"File not found: {file_name}")
        
        # Get updated files from LLM
        print("Requesting updated files from LLM...")
        updated_files = update_code_with_llm(data["brief"], data.get("checks", []), data.get("attachments", []), repo_name, existing_files)
        
        # Ensure each file has the proper SHA if it's an update
        for file in updated_files:
            file_name = file.get("name")
            # If the file doesn't have an action, default to "update"
            if "action" not in file:
                file["action"] = "update"
            
            # If this is an update, make sure we have the SHA
            if file["action"] == "update" and "sha" not in file:
                for existing_file in existing_files:
                    if existing_file["name"] == file_name:
                        file["sha"] = existing_file.get("sha")
                        break
        
        print(f"Pushing {len(updated_files)} files to repo...")
        push_files_to_repo(repo_name, updated_files, data.get("round", 2))
        
        commit_sha = get_sha_of_latest_commit(repo_name)
        evaluation_payload = {
            "email": data["email"],
            "task": data["task"],
            "round": data["round"],
            "nonce": data["nonce"],
            "repo_url": f"https://github.com/{GITHUB_USERNAME}/{repo_name}",
            "commit_sha": commit_sha,
            "pages_url": f"https://{GITHUB_USERNAME}.github.io/{repo_name}/",
        }
        ping_evaluation_server(data["evaluation_url"], evaluation_payload)
        return {"status": "success", "repo_url": evaluation_payload["repo_url"]}
    except Exception as e:
        print(f"Application revision failed: {e}")
        raise e


app = FastAPI()


def process_task_background(data: dict):
    try:
        repo_name = data.get("task")
        print(f"Processing task in background: {repo_name} - Round {data.get('round')}")
        
        # Check if the repository exists and has required files
        repo_exists = repository_exists(repo_name)
        has_required_files = False
        
        if repo_exists:
            print(f"Repository {repo_name} exists, checking for required files...")
            has_required_files = check_repo_has_required_files(repo_name)
            if has_required_files:
                print(f"Repository {repo_name} has all required files")
            else:
                print(f"Repository {repo_name} exists but is missing required files")
        else:
            print(f"Repository {repo_name} does not exist")
        
        # Determine whether this is a creation or update operation
        # If repo exists with required files, it's an update (round > 1)
        # Otherwise, it's an initial creation (round 1)
        if repo_exists and has_required_files:
            print(f"Processing as update operation (Round {data.get('round')})")
            # Use revise_existing_application function for any update operation
            result = revise_existing_application(data)
            print(f"Update operation completed successfully: {result}")
        else:
            print(f"Processing as initial creation operation")
            # Always use build_initial_application for initial creation
            result = build_initial_application(data)
            print(f"Initial creation completed successfully: {result}")
    except Exception as e:
        print(f"Background task processing failed: {e}")


@app.post("/handle_task")
def handle_task(data: dict, background_tasks: BackgroundTasks):
    try:
        if not validate_secret(data.get("secret", "")):
            raise HTTPException(status_code=401, detail="Invalid secret")
        
        required_fields = ["email", "task", "brief", "evaluation_url"]
        for field in required_fields:
            if field not in data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        background_tasks.add_task(process_task_background, data)
        
        return {
            "task": data.get("task"),
            "round": data.get("round"),
            "status": "processing",
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error validating task: {e}")
        raise HTTPException(status_code=500, detail=f"Task validation failed: {str(e)}")

@app.get("/")
def hello():
    return {"text" : "hiiiiiiiiii from dipanshu."}
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)



