# Windows footprint-selection diagnostic build

This build records the interposer, RF and DC footprint-selection sequence
without changing the assignment-cell layout or popup contents. It needs no
administrator privileges.

1. Close PCB Editor, then install the supplied `0.4.18` ZIP with KiCad Plugin
   and Content Manager's **Install from File…** action.
2. Reopen the board and Connector to Interposer Workflow.
3. Open **Connector assignments**, choose a symbol, open the corresponding
   footprint popup and click the intended footprint once.
4. Close the Workflow window. Do not reopen it before copying the log because
   each new Workflow session starts a fresh trace.
5. Press `Win+R` and open:

   `%TEMP%\connector-interposer-workflow-footprint-debug.log`

   PowerShell can open the same file with:

   `notepad "$env:TEMP\connector-interposer-workflow-footprint-debug.log"`

The trace records KiCad, wx and Python versions; popup item IDs; selection
boundaries; callback completion; footprint pad count; and assignment status.
Library identities and paths are represented by length and a short hash. An
exception message or traceback may contain a local path, so review the file
before sharing it.
