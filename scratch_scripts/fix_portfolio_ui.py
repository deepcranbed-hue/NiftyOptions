import re

with open("src/components/PortfolioPanel.tsx", "r") as f:
    code = f.read()

# Make it fetch list if selectedCaptureId is falsy, otherwise value
replacement = """
  const fetchPositions = async () => {
    setLoading(true);
    try {
      let res;
      if (!selectedCaptureId) {
        res = await fetch(`http://127.0.0.1:8000/api/portfolio/list`);
      } else {
        res = await fetch(`http://127.0.0.1:8000/api/portfolio/value?capture_id=${selectedCaptureId}`);
      }
      const data = await res.json();
      if (data.success) {
        // If from list, valuation won't exist. Add dummy valuation.
        const positions = data.positions.map((p: any) => ({
          ...p,
          valuation: p.valuation || { pnl_rupees: 0, pnl_pts: 0, value_a: 0, value_b: 0, error: !selectedCaptureId ? "No snapshot selected" : undefined }
        }));
        setPositions(positions);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPositions();
  }, [selectedCaptureId]);
"""

old_fetch = """
  const fetchPositions = async () => {
    setLoading(true);
    try {
      // Get all open positions valued against the selected capture
      if (!selectedCaptureId) return;
      const res = await fetch(`http://127.0.0.1:8000/api/portfolio/value?capture_id=${selectedCaptureId}`);
      const data = await res.json();
      if (data.success) setPositions(data.positions);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (selectedCaptureId) fetchPositions();
  }, [selectedCaptureId]);
"""

if old_fetch.strip() in code:
    print("WARNING: EXACT MATCH NOT FOUND, TRYING REGEX")

# Doing regex replacement to be safe
import re
code = re.sub(r'const fetchPositions = async \(\) => \{.*?\}, \[selectedCaptureId\]\);', replacement.strip(), code, flags=re.DOTALL)

with open("src/components/PortfolioPanel.tsx", "w") as f:
    f.write(code)

print("done")
