## ADDED Requirements

### Requirement: Message audit logging
The system SHALL log all message processing activities.

#### Scenario: Log received message
- **WHEN** a message is received from Feishu
- **THEN** the system SHALL insert a record into feishu_message_logs
- **AND** record: message_id, event_type, open_id, chat_id, chat_type, raw_content, received_at, status="received"

#### Scenario: Log processing stages
- **WHEN** a message transitions through processing stages
- **THEN** the system SHALL update the status in feishu_message_logs
- **AND** possible statuses: received, buffering, processing, completed, failed, blocked

#### Scenario: Log processing completion
- **WHEN** a message is fully processed
- **THEN** the system SHALL update the record with:
  - status="completed"
  - processed_text (merged text)
  - media_count
  - reply_text
  - reply_length
  - processing_time_ms (from received to reply sent)
  - streaming_card_used (boolean)
  - block_streaming_enabled (boolean)

#### Scenario: Log access denial
- **WHEN** a message is rejected due to access control
- **THEN** the system SHALL log with status="blocked"
- **AND** record the rejection reason: policy violation, user not found, etc.

#### Scenario: Log errors
- **WHEN** an error occurs during processing
- **THEN** the system SHALL log with status="failed"
- **AND** record the error_message
- **AND** record the stack trace (if available)

### Requirement: Processing metrics
The system SHALL track and expose processing metrics.

#### Scenario: Track processing time
- **WHEN** a message is processed
- **THEN** the system SHALL calculate processing_time_ms
- **AND** include: debounce time + user resolution + media download + AI generation + reply sending

#### Scenario: Queue metrics
- **WHEN** Worker is running
- **THEN** the system SHALL expose metrics:
  - queue_length (current Redis list length)
  - messages_processed_total (counter)
  - messages_failed_total (counter)
  - messages_blocked_total (counter)
  - processing_duration_seconds (histogram)

### Requirement: Error tracking
The system SHALL track and alert on errors.

#### Scenario: API error tracking
- **WHEN** Feishu API calls fail
- **THEN** the system SHALL track error counts by endpoint
- **AND** trigger an alert if error rate exceeds 5% in 5 minutes

#### Scenario: Rate limit detection
- **WHEN** Feishu API returns 429 (rate limited)
- **THEN** the system SHALL log the event with level WARNING
- **AND** increment a rate_limit_hits counter
- **AND** trigger an alert if rate limit hits exceed 10 in 1 hour

#### Scenario: Worker failure tracking
- **WHEN** Worker crashes or stops unexpectedly
- **THEN** the system SHALL log the failure
- **AND** trigger an alert
- **AND** record the last processed message for recovery

### Requirement: Session tracking
The system SHALL track user session activity.

#### Scenario: Track session lifecycle
- **WHEN** a session is created (first message from user)
- **THEN** the system SHALL create/update a record in feishu_sessions
- **AND** record: session_id, open_id, chat_id, chat_type, usernumb, app_id, created_at

#### Scenario: Update session activity
- **WHEN** a message is processed for an existing session
- **THEN** the system SHALL update last_message_at timestamp
- **AND** increment message_count for the session

#### Scenario: Session cleanup
- **WHEN** a session has been inactive for 30 days
- **THEN** the system MAY archive the session data
- **AND** clean up associated temporary files

### Requirement: Media file audit
The system SHALL audit media file operations.

#### Scenario: Log media download
- **WHEN** a media file is downloaded
- **THEN** the system SHALL log: message_id, file_key, file_type, file_size, download_duration_ms, status

#### Scenario: Log media access
- **WHEN** a media file is accessed (e.g., by Agent)
- **THEN** the system SHALL update last_accessed_at in feishu_media_files
- **AND** log the access event
