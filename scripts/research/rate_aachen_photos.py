"""Score blinded Aachen photos with number keys and save every change to CSV."""

import argparse
import csv
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk


SCORE = '点数（1〜5）'
ROOT = Path(__file__).resolve().parents[2]


def load_ratings(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        fields = reader.fieldnames
        rows = list(reader)
    required = {'組番号', '表示順', '画像ファイル', SCORE}
    if not fields or not required.issubset(fields) or not rows:
        raise ValueError('採点CSVの列または内容を確認してください。')
    if any(row[SCORE] not in ('', '1', '2', '3', '4', '5') for row in rows):
        raise ValueError('点数は空欄か1〜5で入力してください。')
    return fields, rows


def save_ratings(path, fields, rows):
    with NamedTemporaryFile(dir=path.parent, prefix='.ratings-', suffix='.tmp', mode='w',
                            encoding='utf-8-sig', newline='', delete=False) as file:
        temporary = Path(file.name)
        try:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        except Exception:
            file.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RatingWindow:
    def __init__(self, root, path):
        self.root = root
        self.path = path
        self.fields, self.rows = load_ratings(path)
        self.index = next((i for i, row in enumerate(self.rows) if not row[SCORE]), len(self.rows))
        self.last_edit = None
        self.source = None
        self.resize_job = None
        self.group_count = len({row['組番号'] for row in self.rows})
        self.image = None
        self.root.title('照明の自然さを採点')
        self.root.geometry('1080x760')
        self.root.minsize(640, 500)
        self.root.configure(bg='#181b1f')
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        style = ttk.Style(root)
        style.configure('Rating.TButton', padding=(12, 8))
        self.status = tk.Label(root, font=('Yu Gothic UI', 15, 'bold'), bg='#181b1f', fg='#f4f6f8')
        self.status.grid(row=0, column=0, sticky='ew', pady=(12, 0))
        tk.Label(root, text='その場の光として、明るさ・色・明暗はどれくらい自然に見えますか？',
                 font=('Yu Gothic UI', 11), bg='#181b1f', fg='#e0e4e9').grid(row=1, column=0, sticky='ew', pady=(1, 9))
        self.canvas = tk.Canvas(root, bg='#101215', highlightthickness=0, bd=0)
        self.canvas.grid(row=2, column=0, sticky='nsew', padx=16)
        self.canvas.bind('<Configure>', self.schedule_resize)

        bar = tk.Frame(root, bg='#181b1f')
        bar.grid(row=3, column=0, pady=(9, 4))
        self.buttons = []
        for score in range(1, 6):
            button = ttk.Button(bar, text=str(score), width=4, style='Rating.TButton',
                                command=lambda value=score: self.record(str(value)), takefocus=False)
            button.pack(side='left', padx=4)
            self.buttons.append(button)
        tk.Label(root, text='1 大きな違和感    3 少し違和感    5 自然',
                 font=('Yu Gothic UI', 11), bg='#181b1f', fg='#f4f6f8').grid(row=4, column=0, pady=(1, 2))
        tk.Label(root, text='1〜5  入力して次へ    ← / →  前後へ    Ctrl+Z  直前の採点を取り消す    Esc  閉じる',
                 font=('Yu Gothic UI', 10), bg='#181b1f', fg='#b8c2cc').grid(row=5, column=0, pady=(0, 10))
        root.bind('<KeyPress>', self.key)
        self.show()

    def key(self, event):
        if event.keysym in ('KP_End', 'KP_Down', 'KP_Next', 'KP_Left', 'KP_Begin'):
            self.record(str(('KP_End', 'KP_Down', 'KP_Next', 'KP_Left', 'KP_Begin').index(event.keysym) + 1))
        elif event.char in ('1', '2', '3', '4', '5'):
            self.record(event.char)
        elif event.keysym.startswith('KP_') and event.keysym[3:] in ('1', '2', '3', '4', '5'):
            self.record(event.keysym[3:])
        elif event.keysym == 'Left':
            self.move(-1)
        elif event.keysym == 'Right':
            self.move(1)
        elif event.keysym == 'Escape':
            self.root.destroy()
        elif event.keysym.lower() == 'z' and event.state & 4:
            self.undo()

    def move(self, step):
        self.index = max(0, min(len(self.rows), self.index + step))
        self.show()

    def write_score(self, index, value):
        row = self.rows[index]
        before = row[SCORE]
        row[SCORE] = value
        try:
            save_ratings(self.path, self.fields, self.rows)
        except OSError:
            row[SCORE] = before
            messagebox.showerror('保存できません', '採点CSVをExcelなどで開いている場合は閉じて、もう一度入力してください。',
                                 parent=self.root)
            return False
        return True

    def record(self, value):
        if self.index == len(self.rows) or self.source is None:
            return
        before = self.rows[self.index][SCORE]
        if self.write_score(self.index, value):
            self.last_edit = self.index, before
            self.move(1)

    def undo(self):
        if self.last_edit:
            index, before = self.last_edit
            if self.write_score(index, before):
                self.last_edit = None
                self.index = index
                self.show()

    def show(self):
        count = sum(bool(row[SCORE]) for row in self.rows)
        self.source = None
        if self.index == len(self.rows):
            message = '採点完了' if count == len(self.rows) else '最後の画像まで表示しました'
            self.status.config(text=f'{message}   {count:,} / {len(self.rows):,}')
        else:
            row = self.rows[self.index]
            current = f'    現在の点数 {row[SCORE]}' if row[SCORE] else ''
            self.status.config(text=f'組 {row["組番号"]} / {self.group_count}    写真 {row["表示順"]} / 6'
                                    f'    採点済み {count:,} / {len(self.rows):,}{current}')
            try:
                with Image.open(self.path.parent / row['画像ファイル']) as picture:
                    self.source = picture.convert('RGB')
            except OSError as error:
                messagebox.showerror('画像を開けません', str(error), parent=self.root)
        for button in self.buttons:
            button.configure(state='normal' if self.source else 'disabled')
        self.redraw()

    def schedule_resize(self, _event):
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(70, self.redraw)

    def redraw(self):
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
            self.resize_job = None
        self.canvas.delete('all')
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.source and min(width, height) > 2:
            photo = self.source.copy()
            photo.thumbnail((width, height), Image.Resampling.LANCZOS)
            self.image = ImageTk.PhotoImage(photo)
            self.canvas.create_image(width / 2, height / 2, image=self.image)
        elif self.index == len(self.rows):
            self.canvas.create_text(width / 2, height / 2,
                                    text='点数はCSVに保存されています。\n← で戻る、Esc で閉じる。',
                                    fill='#e8edf4', font=('Yu Gothic UI', 15), justify='center')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, nargs='?', default=ROOT / 'outputs' / 'test2' / '照明採点.csv')
    args = parser.parse_args()
    path = args.csv.resolve()
    root = tk.Tk()
    try:
        with sqlite3.connect(path.with_suffix('.sqlite3'), timeout=0) as lock:
            try:
                lock.execute('BEGIN EXCLUSIVE')
            except sqlite3.OperationalError as error:
                raise ValueError('同じ採点表の画面が開いています。先に開いた画面で続けてください。') from error
            window = RatingWindow(root, path)
            root.mainloop()
    except (OSError, ValueError, sqlite3.Error) as error:
        root.withdraw()
        messagebox.showerror('採点表を開けません', str(error), parent=root)
        root.destroy()
        raise SystemExit(1) from None
