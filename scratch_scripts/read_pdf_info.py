import sys, os
import pypdf

pdf_path = "/Users/deepak/antigravity/investment analysis/8_IAPM_final.pdf"
if not os.path.exists(pdf_path):
    print("PDF not found")
    sys.exit(1)

reader = pypdf.PdfReader(pdf_path)
print("Extracting Chapter 4 details:")
for idx in range(71, 86):
    text = reader.pages[idx].extract_text()
    print(f"\n================ PAGE {idx+1} ================")
    print(text)
