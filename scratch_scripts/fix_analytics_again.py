with open("src/lib/analytics.ts", "r") as f:
    content = f.read()

replacement = """
  } else if (score >= 40) {
    verdict = {
      tone: 'neutral',
      msg: "Elevated Complacency — Premium selling pays less per unit of tail risk; strictly define wing risk.",
    };
  } else {
    verdict = {
      tone: 'neutral',
      msg: "Vol within normal regime — Premium selling is adequately compensated by IV decay.",
    };
  }

  const final_score = Math.round(CONFIG.iv_weight * comp_iv + (1 - CONFIG.iv_weight) * accel);
  const has_oi_data = nearRows.some((r) => !isNaN(r.put_oichg) && Math.abs(r.put_oichg) > 0.001);

  return {
    score: final_score,
    iv: atmIV,
    comp_iv,
    accel,
    has_oi_data,
    bursts: bursts.length,
    max_burst,
    verdict
  };
}

export function generateGlobalCues
"""

content = content.replace("  } else if (score >= 40) {\n    verdict = {\n      tone: 'neutral',\n      msg: \"Elevated Complacency — Premium selling pays less per unit of tail risk; strictly define wing risk.\",\n    };\n\n\nexport function generateGlobalCues", replacement.strip())

with open("src/lib/analytics.ts", "w") as f:
    f.write(content)
