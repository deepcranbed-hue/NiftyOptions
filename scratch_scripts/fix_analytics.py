import re

with open("src/types.ts", "r") as f:
    t_content = f.read()

if "has_oi_data" not in t_content:
    t_content = t_content.replace(
        "bursts: number;",
        "has_oi_data?: boolean;\n  bursts: number;"
    )
    with open("src/types.ts", "w") as f:
        f.write(t_content)

with open("src/lib/analytics.ts", "r") as f:
    a_content = f.read()

# fix the broken edit
a_content = re.sub(r'  } else \{\n    verdict = \{\n  const has_oi_data = nearRows\.some\(\(r\) => !isNaN\(r\.put_oichg\) && Math\.abs\(r\.put_oichg\) > 0\.001\);\n\n  return \{\n    iv: atmIV,\n    comp_iv,\n    accel,\n    has_oi_data,\n    bursts: bursts\.length,\n    max_burst,\n    score,\n    verdict,\n  \};\n\}', '', a_content)

a_content = a_content.replace(
    "return {\n    score: final_score,\n    iv: atmIV,\n    comp_iv,\n    accel,\n    bursts: bursts.length,\n    max_burst,\n    verdict\n  };",
    "const has_oi_data = nearRows.some((r) => !isNaN(r.put_oichg) && Math.abs(r.put_oichg) > 0.001);\n  return {\n    score: final_score,\n    iv: atmIV,\n    comp_iv,\n    accel,\n    has_oi_data,\n    bursts: bursts.length,\n    max_burst,\n    verdict\n  };"
)

with open("src/lib/analytics.ts", "w") as f:
    f.write(a_content)
