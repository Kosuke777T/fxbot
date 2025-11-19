# app/main.py
import tkinter as tk
from tkinter import ttk
from app.gui.dashboard_tab import DashboardTab

def main() -> None:
    root = tk.Tk()
    root.title("FXBot Dashboard")
    root.geometry("520x540")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True)

    dash = DashboardTab(nb)
    nb.add(dash, text="Dashboard")

    root.mainloop()

if __name__ == "__main__":
    main()
