import sys
import os

# Try importing huggingface_hub; install it if it is missing
try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("Installing huggingface_hub library...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface_hub"])
    from huggingface_hub import snapshot_download

def main():
    repo_id = sys.argv[1] if len(sys.argv) > 1 else "baidu/Unlimited-OCR"
    local_dir = "/Users/deepak/antigravity/models/Unlimited-OCR"
    
    print(f"Starting download of '{repo_id}' directly to '{local_dir}'...")
    os.makedirs(local_dir, exist_ok=True)
    
    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=local_dir,
            local_dir_use_symlinks=False
        )
        print("\n[SUCCESS] Model download completed successfully!")
        print(f"All files saved in: {local_dir}")
    except Exception as e:
        print(f"\n[ERROR] Failed to download model: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
