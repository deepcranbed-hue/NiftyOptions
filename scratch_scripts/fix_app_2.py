import re

with open("src/App.tsx", "r") as f:
    content = f.read()

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

if "const onUploadPipeline" not in content:
    content = content.replace(
        "  const runQuantPipeline = async () => {",
        upload_func + "\n  const runQuantPipeline = async () => {"
    )

with open("src/App.tsx", "w") as f:
    f.write(content)
