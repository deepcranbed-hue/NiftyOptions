import zipfile
import os

def check():
    local_path = "/Users/deepak/antigravity/NiftyOptions/SecurityMaster.zip"
    if not os.path.exists(local_path):
        print("SecurityMaster.zip not found locally.")
        return
        
    z = zipfile.ZipFile(local_path)
    with z.open("NSEScripMaster.txt") as f:
        content = f.read().decode('utf-8', errors='ignore')
        lines = content.split('\n')
        print("Headers / First line:")
        print(lines[0])
        print("Second line:")
        print(lines[1])
        
        # Look for RELIANCE
        print("\nRELIANCE match:")
        for line in lines[:500]:
            if "RELIANCE" in line:
                print(line.strip())
                
        # Look for LTIM
        print("\nLTIM match:")
        for line in lines:
            if "LTIM" in line:
                print(line.strip())
                break

if __name__ == "__main__":
    check()
