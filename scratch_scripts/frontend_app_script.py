import re

with open("src/App.tsx", "r") as f:
    content = f.read()

# Add states
state_injection = """
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadSpot, setUploadSpot] = useState<number>(0);
  const [uploadDays, setUploadDays] = useState<number>(0);
"""
content = content.replace("const [pipelineRes, setPipelineRes] = useState<any>(null);", state_injection + "\n  const [pipelineRes, setPipelineRes] = useState<any>(null);")

# Add upload function
upload_func = """
  const onUploadPipeline = async (optConfig?: any) => {
    if (!uploadFile || uploadSpot <= 0 || uploadDays <= 0) {
      alert("Please provide a CSV file, Spot Price, and Days to Expiry (must be > 0).");
      return;
    }
    setIsPipelineRunning(true);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      formData.append("spot", uploadSpot.toString());
      formData.append("days", uploadDays.toString());
      
      const payload = {
        half_life_hours: 12.0,
        risk_cfg: riskConfig,
        book: [],
        current_drawdown_pct: 0.0,
        ...(optConfig || {})
      };
      formData.append("payload", JSON.stringify(payload));

      const res = await fetch("http://127.0.0.1:8000/api/upload-chain", {
        method: "POST",
        body: formData
      });
      if (!res.ok) {
        throw new Error(`Pipeline API Error: ${res.statusText}`);
      }
      const data = await res.json();
      setPipelineRes(data);
    } catch (err: any) {
      console.error(err);
      alert(`Pipeline failed: ${err.message}`);
    } finally {
      setIsPipelineRunning(false);
    }
  };
"""

content = content.replace("const onRunPipeline = async (optConfig?: any) => {", upload_func + "\n\n  const onRunPipeline = async (optConfig?: any) => {")

# Pass props to StrategySuggesterPanel
props_injection = """
            uploadFile={uploadFile}
            setUploadFile={setUploadFile}
            uploadSpot={uploadSpot}
            setUploadSpot={setUploadSpot}
            uploadDays={uploadDays}
            setUploadDays={setUploadDays}
            onUploadPipeline={onUploadPipeline}
"""
content = re.sub(r'(<StrategySuggesterPanel\s+pipelineRes=\{pipelineRes\})', r'\1' + props_injection, content)

with open("src/App.tsx", "w") as f:
    f.write(content)
