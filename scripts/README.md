# scripts/

Put your own build scripts here, for example ones written by Claude. This folder is a
**watch folder**:

- Start the app from the repository root (`python -m pymodeler`). It shows the newest script in
  this folder and rebuilds whenever a `.json` file here is saved. Build errors appear in a banner
  over the view, and the last good model stays on screen.
- Or, without the app: `python -m pymodeler watch` rebuilds each saved script and writes
  `out/<name>.png` (previews) and `out/<name>_report.json` (sizes of named objects).

One-off builds work too:

```bash
python -m pymodeler build scripts/my_model.json --preview out/my_model.png --report
```

See [CLAUDE.md](../CLAUDE.md) for the build-script format and modelling patterns.
