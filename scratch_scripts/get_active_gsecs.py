import urllib.request
from zipfile import ZipFile
from io import BytesIO

def main():
    url = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            zip_file = ZipFile(BytesIO(response.read()))
            for name in zip_file.namelist():
                if "NSE" in name.upper() and not "FO" in name.upper():
                    with zip_file.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        lines = content.splitlines()
                        gsecs = []
                        for line in lines:
                            # Ticker format has many fields. G-Secs have type "GS" (field index 2)
                            parts = [p.strip('"') for p in line.split(',')]
                            if len(parts) > 3 and parts[2] == "GS":
                                # last field is stock code
                                stock_code = parts[-1]
                                desc = parts[3]
                                short_name = parts[1]
                                gsecs.append((stock_code, desc, short_name))
                        
                        # Sort by description/maturity
                        print("List of active G-Secs:")
                        for code, desc, short in sorted(gsecs):
                            print(f"Code: {code:<12} | Short: {short:<8} | Desc: {desc}")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
