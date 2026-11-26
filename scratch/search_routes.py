with open("app/routes.py", "r", encoding="utf-8") as f:
    for idx, line in enumerate(f, 1):
        if "festo" in line.lower():
            print(f"Line {idx}: {line.strip()}")
