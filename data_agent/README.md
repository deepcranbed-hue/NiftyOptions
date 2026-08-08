# Data Agent

This folder contains scripts for downloading and synchronizing data for the NiftyOptions platform.

## FII / DII Historical Data

**Important:** The FII and DII historical flow data is strictly kept in the main **SQLite database** (`option_chains.db`) inside the `fii_dii_flows` table.

- To download/update the FII and DII flows, run `python data_agent/macro/download_fii_dii.py`.
- The frontend UI displays a complete history table of this data by calling the `/api/flows-history` endpoint.
- There is NO PostgreSQL dependency. All data is unified into the main SQLite database.
