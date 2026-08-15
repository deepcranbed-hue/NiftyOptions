import re

with open("src/App.tsx", "r") as f:
    content = f.read()

content = content.replace("import React, { useState, useMemo } from 'react';", "import React, { useState, useMemo } from 'react';\nimport { OptionRow } from './types';")

with open("src/App.tsx", "w") as f:
    f.write(content)
