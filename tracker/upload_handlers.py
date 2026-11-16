"""Bound CSV uploads in memory; never fall through to a disk upload handler."""
from django.core.files.uploadhandler import MemoryFileUploadHandler, StopUpload

from .services import MAX_CSV_BYTES


class BoundedMemoryUploadHandler(MemoryFileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0
        self.file_count = 0

    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        # Do not let an untrusted Content-Length decide whether we spool to disk.
        self.activated = True

    def new_file(self, *args, **kwargs):
        self.file_count += 1
        if self.file_count > 1:
            self.request.csv_upload_error = 'Upload exactly one CSV file at a time.'
            raise StopUpload(connection_reset=True)
        super().new_file(*args, **kwargs)

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > MAX_CSV_BYTES:
            self.request.csv_upload_error = 'Maximum file size is 2 MB.'
            raise StopUpload(connection_reset=True)
        return super().receive_data_chunk(raw_data, start)
