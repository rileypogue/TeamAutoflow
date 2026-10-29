# main graphical user interface using python (tkinter)
# authors: Riley Pogue
# last edited: 10/27/2026

import customtkinter as ctk
import serial
import os
import csv
from pathvalidate import validate_filename, ValidationError
from datetime import datetime
from zoneinfo import ZoneInfo

SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 115200

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

PANEL_BG = "#1e2128"
MUTED = "#8b93a1"

class App(ctk.CTk):
    def __init__(self):
        super().__init__() # call __init__ method from original CTkinter class

        self.geometry("1000x600")
        self.title("Flowbench")
        self.is_running = False
        self.ser = None 
        self.job = None
        self.csv = None
        self.writer = None
        self.csv_title = None

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # header
        header = ctk.CTkFrame(self, height=80, corner_radius=0, fg_color="#14161a")
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(header, text="Flowbench",font=ctk.CTkFont(size=28, weight="bold"),).pack(side="left", padx=(30, 12), pady=20)
        ctk.CTkLabel(header, text="Super Suck time", text_color=MUTED,).pack(side="left", pady=(8, 0))

        
        # body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew",padx=30, pady=30)
        body.grid_columnconfigure((0, 1), weight=1, uniform="panels")
        body.grid_rowconfigure(0, weight=1)

        # left panel
        left = ctk.CTkFrame(body, corner_radius=16, fg_color=PANEL_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.entry = ctk.CTkEntry(
            left, height=45, corner_radius=10,
            placeholder_text="Enter angle for servo position (0-180)",
        )
        self.entry.pack(fill="x", padx=24, pady=(16, 10))
        self.entry.bind("<Return>", lambda event: self.send_value())

        ctk.CTkButton(
            left, text="Send", width=100, height=36,
            command=self.send_value,
        ).pack(anchor="e", padx=24)

        self.feedback = ctk.CTkLabel(left, text="", text_color=MUTED)
        self.feedback.pack(anchor="w", padx=24, pady=(12, 0))

        # right
        right = ctk.CTkFrame(body, corner_radius=16, fg_color=PANEL_BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self.name_entry = ctk.CTkEntry(
            right, height=45, corner_radius=10,
            placeholder_text="Enter file name to save data",
        )
        self.name_entry.pack(fill="x", padx=24, pady=(16, 10))
        self.name_entry.bind("<Return>", lambda event: self.send_name())
        ctk.CTkButton(right, text="Send", width=100, height=36, command=self.send_name,).pack(anchor="e", padx=24)
        self.name_feedback = ctk.CTkLabel(right, text="", text_color=MUTED)
        self.name_feedback.pack(anchor="w", padx=24, pady=(12, 0))

        self.toggle_button = ctk.CTkButton(right, width=300, height=50, fg_color="#28c900", hover_color="#76ba65", text="START", command=self.toggle_state)
        self.toggle_button.pack(expand=True, fill="x", padx=24)

       
        self.toggle_button.configure(state="disabled")
        try:
            self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            self.after(2500, lambda: self.toggle_button.configure(state="normal")) # wait for esp32 reset
        except (serial.SerialException, AttributeError) as e:
            self.ser = None
            self.feedback.configure(text=f"ESP32 not found at {SERIAL_PORT}", text_color="#ff5c5c")

    def toggle_state(self):
        self.is_running = not self.is_running
        if self.is_running:
            self.toggle_button.configure(text="STOP", fg_color="#ff0000", hover_color="#c75656", text_color="white")
            self.start_recording()
        else:
            self.toggle_button.configure(text="START", fg_color="#28c900", hover_color="#76ba65", text_color="black")
            self.end_recording()

    def start_recording(self):
        try:
            self.ser.reset_input_buffer()
            self.ser.write(b"START\n")
        except (serial.SerialException, AttributeError) as e:
            print(f"ERROR: Could not open {SERIAL_PORT}: {e}")
            self.reset_button()
            return

        now = datetime.now(ZoneInfo("America/Denver")).strftime("%m-%d_%H-%M-%S")
        if self.csv_title == None:
            self.csv_title = f"RPM_data_{now}.csv"

        file_is_new = not os.path.exists(self.csv_title) or os.path.getsize(self.csv_title) == 0 # check if file exists or has stuff already in it 

        self.csv =  open(self.csv_title, mode='a', newline='', encoding='utf-8')
        self.writer = csv.writer(self.csv) 
        if file_is_new:
            self.writer.writerow(["esp_us_time", "type", "value"])

        print("opened csv: ", self.csv_title)
        self.poll_serial()

    def drain_serial(self):
        while self.ser.in_waiting > 0:
            line = self.ser.readline().decode("utf-8", errors="replace").strip()
            parts = line.split(",")
            if len(parts) == 3:
                self.writer.writerow(parts)
            self.csv.flush()


    def poll_serial(self):
        if not self.is_running:
            return
        try:
            self.drain_serial() 
        except serial.SerialException as e:
            print(f"Serial read failed: {e}")
            self.is_running = False
            self.end_recording()
            self.reset_button()
            return

        self.job = self.after(50, self.poll_serial) # run again in 50 ms

    def end_recording(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None 
        try:
            self.ser.write("STOP\n".encode())
        except (serial.SerialException, AttributeError):
            pass
        self.toggle_button.configure(state="disabled")
        self.after(200, self.finish_recording)


    def finish_recording(self):
        if self.csv:
            try:
                self.drain_serial()
            except (serial.SerialException, AttributeError):
                pass
            self.csv.close()
            self.csv = None
            self.writer = None
        self.toggle_button.configure(state="normal")


    def send_name(self):
        text = self.name_entry.get().strip()
        try:
            validate_filename(text)
        except ValidationError as e:
            self.name_feedback.configure(text=f"{text} is not a valid filename: {e}", text_color="#ff5c5c")
            return
        idx = text.find(".")
        if idx == -1:
            text = text + ".csv"
        elif text[idx:] != ".csv":
            text = text[:idx] + ".csv"
        self.name_feedback.configure(text=f"Saving {self.csv_title}... ")
        self.csv_title = text


    def send_value(self):
        text = self.entry.get().strip()
        try:
            value = int(text)
        except ValueError:
            self.feedback.configure(text="Please enter whole number... ", text_color="#ff5c5c")
            return 
        if not 0 <= value <= 180: 
            self.feedback.configure(text="Number should be between 0 and 180", text_color="#ff5c5c")
            return
        try:
            self.ser.write(f"SPEED {text}\n".encode())
        except (serial.SerialException, AttributeError) as e:
            self.feedback.configure(text=f"Did not send speed, ESP32 not connected: {e}", text_color="#ff5c5c")
            return
        self.feedback.configure(text=f"Speed: {text}")
        self.entry.delete(0,"end") # clear text entry box

    def on_close(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.write(b"SPEED 0\n") # return throttle to 0
                if self.is_running:
                    self.ser.write(b"STOP\n")
                self.ser.close()
        except (serial.SerialException, AttributeError):
            pass
        finally:
            if self.job:
                self.after_cancel(self.job)
            if self.csv:
                self.csv.close()
            self.destroy()

    def reset_button(self):
        self.is_running = False
        self.toggle_button.configure(text="START", state="normal", fg_color="#28c900", hover_color="#76ba65", text_color="black")


if __name__ == "__main__":
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.on_close()

     
