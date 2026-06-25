import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';
import { GoogleGenAI } from '@google/genai';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;

app.use(express.json({ limit: '10mb' }));

// Lazy AI client initialization
let aiClient: GoogleGenAI | null = null;
function getAIClient(): GoogleGenAI {
  if (!aiClient) {
    const key = process.env.GEMINI_API_KEY;
    if (!key) {
      throw new Error('GEMINI_API_KEY environment variable is not configured.');
    }
    aiClient = new GoogleGenAI({ apiKey: key });
  }
  return aiClient;
}

// API Route for Nifty Desk AI Copilot
app.post('/api/analyze-desk', async (req, res) => {
  try {
    const {
      chainRows,
      spot,
      maxPain,
      pcr,
      complacencyScore,
      complacencyVerdict,
      globalCues,
      newsSentiment,
      traderOutlook,
      capital
    } = req.body;

    const ai = getAIClient();
    
    const prompt = `You are the Chief Quantitative Derivatives Strategist at an institutional Nifty 50 options desk.
Analyze the following live NIFTY options positioning chain, complacency gauge, global macroeconomic cues, and sector news sentiment.

=== CURRENT MARKET METRICS ===
• Estimated Nifty Spot: ₹${spot}
• Max Pain Strike: ₹${maxPain} (Diff: ${maxPain - spot > 0 ? '+' : ''}${Math.round(maxPain - spot)})
• Put-Call Ratio (OI): ${pcr}
• Complacency Score: ${complacencyScore}/100 (${complacencyVerdict.tone}: ${complacencyVerdict.msg})
• Trader Outlook Input: ${traderOutlook}
• Available Capital: ₹${capital}

=== GLOBAL MACRO CUES ===
${JSON.stringify(globalCues, null, 2)}

=== NET SECTOR SENTIMENT ===
${JSON.stringify(newsSentiment, null, 2)}

=== TOP OPTION CHAIN STRIKES (Sampled around spot) ===
${JSON.stringify(chainRows.slice(0, 15), null, 2)}

Provide a sharp, institutional trading desk memo formatted in crisp Markdown with the following sections:
1. **Executive Market Structure**: Immediate take on writer positioning, PCR tilt, and max pain gravity.
2. **Vol Complacency & Tail Risk**: Are option writers crowding cheap vol? Is owning optionality favored over selling?
3. **Sector & Global Interplay**: How US/Asian macro moves connect with today's domestic sector sentiment.
4. **Optimal Position Recommendations**: Suggest 2 exact option strategies (e.g. Iron Condor, Call Spread, Strangle) with recommended Nifty strike prices (rounded to 50s), DTE guidance, and risk/reward rationale.
5. **Desk Defense & Greeks Hedging**: Concrete rules for managing tested wings or delta spikes.

Keep the tone professional, objective, institutional, and actionable. Note that this is quantitative desk analysis, not retail financial advice.`;

    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: prompt,
    });

    res.json({ success: true, analysis: response.text });
  } catch (error: any) {
    console.error('AI Analysis Error:', error);
    res.status(500).json({
      success: false,
      error: error.message || 'Failed to generate desk analysis.'
    });
  }
});

// Health check
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', time: new Date().toISOString() });
});

async function startServer() {
  if (process.env.NODE_ENV !== 'production') {
    const { createServer: createViteServer } = await import('vite');
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*all', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 Nifty Options Desk Server running on http://localhost:${PORT}`);
  });
}

startServer();
