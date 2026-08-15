import urllib.request
import zipfile
import io

def check():
    url = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
    print(f"Downloading security master from {url}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        zip_data = response.read()
        
    print("Extracting ZIP contents...")
    z = zipfile.ZipFile(io.BytesIO(zip_data))
    
    # Let's search inside CDNSEScripMaster.txt and MCXScripMaster.txt
    for name in z.namelist():
        if "CDNSE" in name or "MCX" in name:
            print(f"\nScanning file: {name}")
            with z.open(name) as f:
                content = f.read().decode('utf-8', errors='ignore')
                lines = content.split('\n')
                print(f"Total lines in {name}: {len(lines)}")
                
                # Print first 20 matches for USDINR or GOLD or CRUDEOIL
                matches = 0
                for line in lines:
                    if "USDINR" in line or "GOLD" in line or "CRUDEOIL" in line:
                        # Only show futures contracts (FUT)
                        if "FUT" in line:
                            print(f"MATCH: {line.strip()}")
                            matches += 1
                            if matches >= 20:
                                break

if __name__ == "__main__":
    check()
