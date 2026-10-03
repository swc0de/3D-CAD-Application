# scripts/

Put your own build scripts here (for example ones written by Claude). In Phase 7 the app
will watch this folder and rebuild the model live whenever a `.json` file here is saved.
Until then, build them from the command line:

```bash
python -m pymodeler build scripts/my_model.json --preview out/my_model.png --report
```
