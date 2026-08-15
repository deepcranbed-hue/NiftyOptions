import os
import sys
import tempfile
import fitz  # PyMuPDF
import torch

# 1. Force CPU device to support BFloat16 operations safely on macOS
device = torch.device("cpu")
print(f"Using device: {device}")

# Patch Tensor.cuda to return Tensor.to(device)
def patched_cuda(self, *args, **kwargs):
    return self.to(device)
torch.Tensor.cuda = patched_cuda

# Patch torch.autocast to run without CUDA autocasting
class PatchedAutocast:
    def __init__(self, device_type, *args, **kwargs):
        self.enabled = False
    def __enter__(self):
        pass
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
torch.autocast = PatchedAutocast

from transformers import AutoModel, AutoTokenizer

def pdf_to_images(pdf_path, max_pages=3, dpi=200):
    print(f"Extracting first {max_pages} pages of PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    tmp_dir = tempfile.mkdtemp(prefix='pdf_ocr_')
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    paths = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        out = os.path.join(tmp_dir, f'page_{i+1:04d}.png')
        page.get_pixmap(matrix=mat).save(out)
        paths.append(out)
    doc.close()
    return paths

def main():
    pdf_path = "/Users/deepak/antigravity/A2A_CMBS_Framework/CMBSdocumentparser/sec.gov_Archives_edgar_data_1754913_000153949718002047_n1428_424b2-x8.htm.pdf"
    model_path = "/Users/deepak/antigravity/models/Unlimited-OCR"
    output_dir = "/Users/deepak/antigravity/A2A_CMBS_Framework/CMBSdocumentparser/extracted"
    os.makedirs(output_dir, exist_ok=True)
    
    if not os.path.exists(pdf_path):
        print(f"[ERROR] Target PDF not found: {pdf_path}")
        sys.exit(1)
        
    print("Loading tokenizer and model...")
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    
    # Load model in bfloat16 for full CPU capability
    model = AutoModel.from_pretrained(
        model_path,
        trust_remote_code=True,
        use_safetensors=True,
        torch_dtype=torch.bfloat16
    )
    model = model.eval().to(device)
    
    # Convert PDF pages to PNG images (first page only)
    image_paths = pdf_to_images(pdf_path, max_pages=1, dpi=200)
    
    print("\nStarting OCR processing with Unlimited-OCR...")
    for idx, img_path in enumerate(image_paths):
        page_num = idx + 1
        print(f"\n--- Processing Page {page_num} ({img_path}) ---")
        
        # Call model's single-image inference helper
        try:
            parsed_text = model.infer(
                tokenizer,
                prompt='<image>document parsing.',
                image_file=img_path,
                output_path=output_dir,
                base_size=1024,
                image_size=640,
                crop_mode=True,
                max_length=4096,
                no_repeat_ngram_size=35,
                ngram_window=128,
                save_results=False  # We will save manually to a clean filename
            )
            
            output_file = os.path.join(output_dir, f"page_{page_num}.md")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(parsed_text)
                
            print(f"[SUCCESS] Parsed Page {page_num} saved to: {output_file}")
            print(f"--- Page {page_num} Sample Content ---")
            print(parsed_text[:500])
            print("---------------------------------------")
            
        except Exception as e:
            print(f"[ERROR] Failed to process Page {page_num}: {e}")

if __name__ == "__main__":
    main()
