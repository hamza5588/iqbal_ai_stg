import os
import csv
from datetime import datetime
from flask import current_app
from app.utils.db import get_db, get_session_factory
from app.load_testing.models import TestMessageCSV
import logging

logger = logging.getLogger(__name__)

class MessageCSVManager:
    @staticmethod
    def get_upload_dir():
        upload_dir = os.path.join(current_app.root_path, '..', 'tests', 'assets', 'message_csvs')
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir)
        return upload_dir

    @staticmethod
    def _read_csv_content(file_path):
        """Try reading CSV with multiple encodings"""
        # Check if it's a ZIP file (Excel XLSX files are ZIPs)
        try:
            with open(file_path, 'rb') as f:
                header = f.read(4)
                if header == b'PK\x03\x04':
                    return None, "File appears to be an Excel Workbook (.xlsx) misnamed as .csv. Please export it as 'CSV (Comma delimited)'."
        except Exception as e:
            logger.error(f"Error checking file header: {e}")

        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'mac-roman', 'utf-16']
        for enc in encodings:
            try:
                messages = []
                with open(file_path, mode='r', encoding=enc) as f:
                    reader = csv.reader(f)
                    all_rows = list(reader)
                    
                    if not all_rows:
                        continue
                        
                    # Skip header if first row is a common header label
                    start_idx = 0
                    first_row = all_rows[0]
                    if first_row and len(all_rows) > 1:
                        header_candidate = first_row[0].strip().lower()
                        if header_candidate in ['message', 'messages', 'prompt', 'prompts', 'text', 'question', 'questions']:
                            start_idx = 1
                            
                    for row in all_rows[start_idx:]:
                        if row:
                            msg = row[0].strip()
                            # Basic validation: ignore if row is empty after strip or contains null bytes (binary garbage)
                            if msg and '\0' not in msg:
                                messages.append(msg)
                
                # If we got reasonable looking text, return it
                if messages:
                    return messages, None
            except UnicodeDecodeError:
                continue
            except Exception as e:
                # Only return error if we've exhausted all encodings or it's a fatal error
                if enc == encodings[-1]:
                    return None, f"Error with {enc}: {str(e)}"
        
        return None, "Unable to decode CSV or no valid messages found. Please ensure it is a plain text CSV file."

    @staticmethod
    def create_message_csv(name, file):
        """Save an uploaded CSV file and record it in the DB"""
        db = get_db()
        try:
            upload_dir = MessageCSVManager.get_upload_dir()
            timestamp = int(datetime.utcnow().timestamp())
            filename = f"{timestamp}_{file.filename}"
            file_path = os.path.join(upload_dir, filename)
            
            file.save(file_path)
            
            # Count messages
            messages, error = MessageCSVManager._read_csv_content(file_path)
            if error:
                logger.error(f"Error reading CSV: {error}")
                count = 0
            else:
                count = len(messages)
            
            csv_record = TestMessageCSV(
                name=name,
                file_path=file_path,
                message_count=count
            )
            db.add(csv_record)
            db.commit()
            return csv_record, None
        except Exception as e:
            logger.error(f"Failed to create message CSV: {e}")
            db.rollback()
            return None, str(e)

    @staticmethod
    def get_messages(csv_id):
        """Read all messages from a CSV"""
        db = get_db()
        csv_record = db.query(TestMessageCSV).get(csv_id)
        if not csv_record or not os.path.exists(csv_record.file_path):
            return []
            
        messages, error = MessageCSVManager._read_csv_content(csv_record.file_path)
        if error:
            logger.error(f"Error reading CSV {csv_id}: {error}")
            return []
            
        return messages

    @staticmethod
    def get_all_csvs():
        db = get_db()
        return db.query(TestMessageCSV).order_by(TestMessageCSV.created_at.desc()).all()

    @staticmethod
    def delete_message_csv(csv_id):
        db = get_db()
        csv_record = db.query(TestMessageCSV).get(csv_id)
        if csv_record:
            if os.path.exists(csv_record.file_path):
                os.remove(csv_record.file_path)
            db.delete(csv_record)
            db.commit()
            return True
        return False
