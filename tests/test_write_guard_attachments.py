"""Turn attachment → MCP file argument resolution."""

from __future__ import annotations

import base64
import unittest

from app.attachment_upload import file_from_turn_attachment, resolve_file_arguments
from app.turn_context import set_turn_attachments


class WriteGuardAttachmentTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_turn_attachments([])

    def test_resolves_attachment_id_to_inline_base64(self) -> None:
        payload = b"png-bytes"
        set_turn_attachments(
            [
                {
                    "id": "att123",
                    "name": "cover.png",
                    "mime": "image/png",
                    "size": len(payload),
                    "_bytes": payload,
                }
            ]
        )

        resolved = file_from_turn_attachment("att123")
        self.assertIsInstance(resolved, dict)
        assert isinstance(resolved, dict)
        self.assertEqual(resolved["filename"], "cover.png")
        self.assertEqual(resolved["encoding"], "base64")
        self.assertEqual(resolved["mime_type"], "image/png")
        self.assertEqual(base64.b64decode(resolved["content"]), payload)

    def test_resolves_attachment_by_filename(self) -> None:
        payload = b"hello"
        set_turn_attachments(
            [
                {
                    "id": "att456",
                    "name": "notes.txt",
                    "mime": "text/plain",
                    "size": len(payload),
                    "_bytes": payload,
                }
            ]
        )

        resolved = file_from_turn_attachment("notes.txt")
        self.assertIsInstance(resolved, dict)
        assert isinstance(resolved, dict)
        self.assertEqual(resolved["filename"], "notes.txt")
        self.assertEqual(base64.b64decode(resolved["content"]), payload)

    def test_leaves_urls_and_unknown_ids_alone(self) -> None:
        set_turn_attachments([])
        url = "https://example.com/a.png"
        self.assertEqual(file_from_turn_attachment(url), url)
        self.assertEqual(file_from_turn_attachment("missing-id"), "missing-id")
        already = {"filename": "a.png", "content": "YQ==", "encoding": "base64"}
        self.assertEqual(file_from_turn_attachment(already), already)

    def test_resolve_file_arguments_rewrites_file_key(self) -> None:
        payload = b"x"
        set_turn_attachments(
            [
                {
                    "id": "att789",
                    "name": "x.bin",
                    "mime": "application/octet-stream",
                    "size": 1,
                    "_bytes": payload,
                }
            ]
        )
        args = resolve_file_arguments(
            {"bucket_id": "uploads", "file_id": "unique()", "file": "att789"}
        )
        self.assertEqual(args["bucket_id"], "uploads")
        self.assertIsInstance(args["file"], dict)
        self.assertEqual(args["file"]["filename"], "x.bin")


if __name__ == "__main__":
    unittest.main()
