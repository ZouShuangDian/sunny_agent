## ADDED Requirements

### Requirement: BlockStreaming accumulation
The system SHALL accumulate AI-generated text before flushing to Feishu.

#### Scenario: Accumulate until min_chars reached
- **WHEN** AI starts generating text
- **THEN** the system SHALL accumulate tokens in a buffer
- **AND** wait until the buffer reaches min_chars (default 800) before flushing
- **AND** flush the buffer when min_chars is reached

#### Scenario: Force flush on max_chars
- **WHEN** the buffer reaches max_chars (default 1200)
- **THEN** the system SHALL immediately flush the buffer
- **AND** continue accumulating for the next block

#### Scenario: Idle flush
- **WHEN** no new tokens are received for idle_ms (default 1000ms)
- **AND** the buffer has content (length > 0)
- **THEN** the system SHALL flush the buffer even if min_chars is not reached

### Requirement: Streaming card creation and updates
The system SHALL create and update Feishu streaming cards in real-time.

#### Scenario: Create streaming card
- **WHEN** the first flush occurs
- **THEN** the system SHALL create a streaming card via POST /cardkit/v1/cards
- **AND** send the card as a message to the chat
- **AND** save the card_id and message_id

#### Scenario: Update streaming card text
- **WHEN** new content is flushed from the buffer
- **THEN** the system SHALL update the streaming card via PUT /cardkit/v1/cards/{card_id}/elements/{element_id}/content
- **AND** increment the sequence number
- **AND** update the card content every 50ms with 2 characters (configurable)

#### Scenario: Close streaming mode
- **WHEN** AI generation completes
- **THEN** the system SHALL close streaming mode via PATCH /cardkit/v1/cards/{card_id}/settings
- **AND** set streaming_mode to false
- **AND** update the summary with a truncated version of the final text (first 50 chars)

### Requirement: Long text chunking
The system SHALL split long replies into multiple chunks.

#### Scenario: Text exceeds chunk threshold
- **WHEN** the final text exceeds chunk_size (default 2000 characters)
- **THEN** the system SHALL split the text into chunks using paragraph boundary detection
- **AND** send the first chunk via streaming card
- **AND** send subsequent chunks as regular text messages

#### Scenario: Paragraph boundary detection
- **WHEN** chunking text with mode "paragraph"
- **THEN** the system SHALL split on "\n\n" (double newline)
- **AND** ensure each chunk is as close to chunk_size as possible without breaking paragraphs
- **AND** use "\n\n" as the joiner between chunks

### Requirement: BlockStreaming configuration
The system SHALL support configurable BlockStreaming parameters.

#### Scenario: Custom min_chars and idle_ms
- **WHEN** feishu_access_config.block_streaming_config specifies custom values
- **THEN** the system SHALL use the configured min_chars, max_chars, and idle_ms
- **AND** the values SHALL be validated (min_chars >= 1, max_chars >= min_chars, idle_ms >= 0)

#### Scenario: Disable block streaming
- **WHEN** block_streaming_config.enabled is set to false
- **THEN** the system SHALL send the complete reply as a single message without streaming

### Requirement: Error handling in streaming
The system SHALL handle streaming card API failures gracefully.

#### Scenario: Streaming card creation fails
- **WHEN** creating a streaming card fails after 3 retries
- **THEN** the system SHALL fall back to regular message mode
- **AND** send "正在思考，请稍候..." as a placeholder
- **AND** send the complete reply when ready

#### Scenario: Streaming update fails
- **WHEN** updating the streaming card fails
- **THEN** the system SHALL log a warning
- **AND** continue accumulating text
- **AND** retry on the next flush
- **AND** if failures persist, fall back to sending the complete text as a regular message
