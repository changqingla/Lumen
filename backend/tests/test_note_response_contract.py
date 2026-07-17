import os
from datetime import datetime, timezone
from uuid import uuid4

os.environ["DEBUG"] = "false"

from modules.notes.entities.note import Note, NoteFolder
from modules.notes.schemas import NoteItem, UpdateNoteRequest


def test_note_response_includes_its_folder_id():
    folder_id = uuid4()
    user_id = uuid4()
    timestamp = datetime.now(timezone.utc)
    folder = NoteFolder(id=folder_id, user_id=user_id, name="Work")
    note = Note(
        id=uuid4(),
        user_id=user_id,
        folder_id=folder_id,
        title="Contract",
        content="Folder identity must survive serialization.",
        tags=[],
        created_at=timestamp,
        updated_at=timestamp,
    )
    note.folder = folder

    payload = note.to_dict()
    validated = NoteItem.model_validate(payload)

    assert payload["folderId"] == str(folder_id)
    assert validated.folderId == str(folder_id)
    assert NoteItem.model_fields["folderId"].is_required()
    assert not UpdateNoteRequest.model_fields["folderId"].is_required()
