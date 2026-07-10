import os
import pathlib
import subprocess

pathlib.Path("results").mkdir(exist_ok=True)
secret = os.environ.get("MY_SECRET", "<MISSING>")
msg = f"job ran on pod; MY_SECRET={secret}"
print(msg)
pathlib.Path("results/out.txt").write_text(msg + "\n")

try:
    g = subprocess.check_output(["nvidia-smi", "-L"], text=True).strip()
except Exception as e:
    g = f"no nvidia-smi: {e}"
print("GPU:", g)
pathlib.Path("results/gpu.txt").write_text(g + "\n")
