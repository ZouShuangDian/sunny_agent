## ADDED Requirements

### Requirement: Download media from Feishu
The system SHALL download media files attached to Feishu messages.

#### Scenario: Download image file
- **WHEN** a message contains an image (msg_type = "image")
- **THEN** the system SHALL extract the image_key from the message content
- **AND** call Feishu API GET /im/v1/messages/{message_id}/resources/{image_key}?type=image
- **AND** stream the response to a local file

#### Scenario: Download file attachment
- **WHEN** a message contains a file (msg_type = "file")
- **THEN** the system SHALL extract the file_key from the message content
- **AND** call Feishu API GET /im/v1/messages/{message_id}/resources/{file_key}?type=file
- **AND** stream the response to a local file

#### Scenario: File size limit
- **WHEN** downloading a file that exceeds 30MB
- **THEN** the system SHALL abort the download
- **AND** log a warning about the oversized file
- **AND** continue processing the text portion of the message
- **AND** annotate in the prompt: "[文件过大，无法下载]"

#### Scenario: Download failure
- **WHEN** the media download fails (network error, file not found, etc.)
- **THEN** the system SHALL retry up to 3 times
- **AND** if still failing, log the error
- **AND** continue processing without the media
- **AND** annotate in the prompt: "[文件下载失败]"

### Requirement: Store media files locally
The system SHALL store downloaded media files with proper organization.

#### Scenario: Save to user directory
- **WHEN** a media file is successfully downloaded
- **THEN** the system SHALL save it to {SANDBOX_HOST_VOLUME}/uploads/feishu_media/{user_id}/
- **AND** generate a filename: {file_type}_{hash}_{timestamp}.{ext}
- **AND** ensure the directory exists (create if not exists)

#### Scenario: Calculate file hash
- **WHEN** saving a media file
- **THEN** the system SHALL calculate the SHA256 hash while streaming (8KB chunks)
- **AND** use the hash as part of the filename for deduplication

#### Scenario: Create database record
- **WHEN** a media file is saved
- **THEN** the system SHALL insert a record into feishu_media_files table
- **AND** record: message_id, file_key, file_name, file_type, file_size, local_path, file_hash, user_id, open_id, chat_id, downloaded_at

### Requirement: Media file lifecycle
The system SHALL manage media file retention and access.

#### Scenario: Update last accessed timestamp
- **WHEN** a media file is referenced in a conversation
- **THEN** the system SHALL update the last_accessed_at timestamp in the database

#### Scenario: Prevent duplicate downloads
- **WHEN** attempting to download a file with the same file_key for the same message
- **AND** a record already exists in feishu_media_files
- **THEN** the system SHALL reuse the existing file
- **AND** update the last_accessed_at timestamp
- **AND** not re-download

### Requirement: Media types support
The system SHALL support various media types from Feishu.

#### Scenario: Supported media types
- **WHEN** receiving a message with msg_type in ["image", "file", "audio", "media", "sticker"]
- **THEN** the system SHALL attempt to download and process the media

#### Scenario: Unsupported media type
- **WHEN** receiving a message with an unsupported msg_type
- **THEN** the system SHALL log a warning
- **AND** continue processing the text portion only
