from typing import List, Dict, Optional, Tuple
from kitty.key_encoding import KeyEvent
import httpx
import json
import os
from pathlib import Path
import asyncio
from kitty.boss import Boss
import difflib
import tempfile
import subprocess

class FileEditor:
    def __init__(self):
        self.editor = os.environ.get('EDITOR', 'vim')
        
    def generate_diff(self, original: str, modified: str, filepath: str) -> str:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=filepath,
            tofile=filepath
        )
        return ''.join(diff)
        
    def apply_changes(self, filepath: str, new_content: str) -> bool:
        try:
            with open(filepath, 'w') as f:
                f.write(new_content)
            return True
        except Exception:
            return False
            
    def edit_file(self, filepath: str) -> Optional[str]:
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filepath)[1]) as tf:
            with open(filepath) as f:
                tf.write(f.read().encode())
                tf.flush()
            subprocess.call([self.editor, tf.name])
            tf.seek(0)
            return tf.read().decode()

class Handler:
    def __init__(self):
        self.api_key = os.environ.get('OPENROUTER_API_KEY')
        self.messages = []
        self.current_input = []
        self.scroll_pos = 0
        self.context_files = []
        self.file_editor = FileEditor()
        self.mode = 'chat'  # chat, review, edit
        self.proposed_changes: Dict[str, str] = {}
        self.command_history = []
        self.command_index = 0
        
    def initialize(self) -> None:
        self.cmd.set_window_title('AI Assistant')
        self.cmd.set_cursor_visible(True)
        self.help_text = """
Commands:
/context file1,file2,...  - Set context files
/edit file               - Edit file
/approve                 - Apply proposed changes
/reject                  - Reject proposed changes
/clear                  - Clear chat history
/help                   - Show this help
↑↓                      - Navigate command history
PgUp/PgDn               - Scroll chat
"""
        self.draw_screen()

    def get_file_content(self, path: str) -> str:
        try:
            with open(path, 'r') as f:
                return f.read()
        except Exception as e:
            return f"Error reading {path}: {str(e)}"

    async def process_edit_suggestion(self, message: Dict[str, str]) -> None:
        content = message['content']
        try:
            changes = json.loads(content)
            for filepath, new_content in changes.items():
                original = self.get_file_content(filepath)
                diff = self.file_editor.generate_diff(original, new_content, filepath)
                self.proposed_changes[filepath] = new_content
                self.messages.append({
                    "role": "system",
                    "content": f"Proposed changes for {filepath}:\n{diff}\nUse /approve to apply or /reject to reject"
                })
        except json.JSONDecodeError:
            self.messages.append({
                "role": "system",
                "content": "Failed to parse edit suggestion. Expected JSON format."
            })

    async def send_message(self, message: str) -> None:
        context = "\n\n".join(f"Content of {path}:\n{self.get_file_content(path)}" 
                            for path in self.context_files)
        
        system_message = """You are an AI assistant in a terminal environment. 
        To suggest file edits, respond with a JSON object mapping filepaths to their new content."""
        
        messages = [{"role": "system", "content": system_message}] + self.messages
        messages.append({"role": "user", "content": f"Context:\n{context}\n\nUser message:\n{message}"})
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "anthropic/claude-3-opus-20240229",
                    "messages": messages
                }
            )
            
        result = response.json()
        assistant_message = result['choices'][0]['message']
        
        self.messages.append({"role": "user", "content": message})
        self.messages.append(assistant_message)
        
        if message.startswith('/edit'):
            await self.process_edit_suggestion(assistant_message)
            
        self.draw_screen()

    def handle_command(self, command: str) -> None:
        if command.startswith('/context '):
            self.context_files = command[9:].strip().split(',')
            self.messages.append({"role": "system", "content": f"Context set to: {self.context_files}"})
        elif command.startswith('/edit '):
            filepath = command[6:].strip()
            if os.path.exists(filepath):
                self.mode = 'edit'
                asyncio.create_task(self.send_message(f"Please suggest edits for {filepath}"))
        elif command == '/approve':
            for filepath, content in self.proposed_changes.items():
                if self.file_editor.apply_changes(filepath, content):
                    self.messages.append({"role": "system", "content": f"Applied changes to {filepath}"})
                else:
                    self.messages.append({"role": "system", "content": f"Failed to apply changes to {filepath}"})
            self.proposed_changes.clear()
        elif command == '/reject':
            self.messages.append({"role": "system", "content": "Rejected proposed changes"})
            self.proposed_changes.clear()
        elif command == '/clear':
            self.messages = []
        elif command == '/help':
            self.messages.append({"role": "system", "content": self.help_text})

    def on_key(self, key_event: KeyEvent) -> bool:
        if key_event.key == 'ENTER':
            message = ''.join(self.current_input)
            if message:
                self.command_history.append(message)
                self.command_index = len(self.command_history)
                if message.startswith('/'):
                    self.handle_command(message)
                else:
                    asyncio.create_task(self.send_message(message))
                self.current_input = []
        elif key_event.key == 'BACKSPACE':
            if self.current_input:
                self.current_input.pop()
        elif key_event.key == 'UP':
            if self.command_index > 0:
                self.command_index -= 1
                self.current_input = list(self.command_history[self.command_index])
        elif key_event.key == 'DOWN':
            if self.command_index < len(self.command_history) - 1:
                self.command_index += 1
                self.current_input = list(self.command_history[self.command_index])
        elif key_event.key == 'PAGE_UP':
            self.scroll_pos = max(0, self.scroll_pos - 1)
        elif key_event.key == 'PAGE_DOWN':
            self.scroll_pos = min(len(self.messages), self.scroll_pos + 1)
        else:
            self.current_input.append(key_event.text)
            
        self.draw_screen()
        return True

handle = Handler
