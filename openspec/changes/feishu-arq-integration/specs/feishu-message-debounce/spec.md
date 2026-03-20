## ADDED Requirements

### Requirement: ARQ Task-based debounce
The system SHALL implement debounce logic within the ARQ Task execution context.

#### Scenario: Debounce in ARQ Task context
- **WHEN** `process_feishu_message` ARQ Task is executed
- **THEN** the debounce logic SHALL run inside the Task
- **AND** utilize ARQ's built-in retry mechanism if debounce state check fails
- **AND** leverage ARQ's timeout handling for long debounce waits

### Requirement: Message transfer without debounce (Updated for BRPOPLPUSH long-running task)
**DEPRECATED**: The original design used Cron Job with pre-debounce check. This has been replaced with **BRPOPLPUSH + processing:queue** long-running task for better reliability (see P0-1/P0-3 fixes).

The system SHALL transfer messages from Redis List to ARQ Queue immediately without debounce checks.

#### Rationale
- **BRPOPLPUSH** atomic operation ensures message durability (P0-3 fix)
  - Unlike BRPOP which pops and loses message on failure, BRPOPLPUSH atomically moves message to processing:queue
  - Even if Worker crashes, message remains in processing:queue for recovery
- Long-running task replaces Cron Job for lower latency (P0-1 fix)
- Debounce logic is consolidated into ARQ Task for simpler architecture

#### Scenario: Immediate message transfer with BRPOPLPUSH
- **WHEN** `message_transfer_loop` (**BRPOPLPUSH** long-running task) receives a message
- **THEN** it SHALL atomically move message from `feishu:webhook:queue` to `feishu:processing:queue`
- **AND** process message from `feishu:processing:queue` (幂等检查, 入队 ARQ)
- **AND** upon successful ARQ enqueue, remove message from `feishu:processing:queue` (LREM)
- **AND** upon failure, keep message in `feishu:processing:queue` for retry or manual recovery
- **AND** use ACK mechanism (processing/processed keys) to prevent duplicate processing
- **AND** debounce SHALL be handled entirely within `process_feishu_message` ARQ Task

### Requirement: Inbound message debounce (Time-based)
The system SHALL buffer and merge consecutive messages from the same user within a time window.

#### Scenario: Buffer consecutive messages
- **WHEN** a message is received from user A
- **AND** the user's state is "idle"
- **AND** should_debounce hook returns True (or not configured)
- **THEN** the system SHALL set the state to "buffering"
- **AND** add the message to the buffer queue (Redis List)
- **AND** start a debounce timer (default 2 seconds)

#### Scenario: Extend debounce period
- **WHEN** a new message arrives from the same user while in "buffering" state
- **THEN** the system SHALL cancel the previous timer
- **AND** create a new timer with full debounce_wait_seconds
- **AND** add the new message to the buffer queue

#### Scenario: Debounce timeout
- **WHEN** the debounce timer expires (no new messages for debounce_wait_seconds)
- **THEN** the system SHALL merge all buffered messages
- **AND** enqueue the merged message to ARQ Queue as a single Task
- **AND** the ARQ Task SHALL process the merged message

#### Scenario: Processing while buffering
- **WHEN** a user sends messages while their previous request is still "processing"
- **THEN** the new messages SHALL be added to the buffer queue
- **AND** processed after the current request completes
- **AND** the system SHALL wait for state to become "idle" before starting new debounce

### Requirement: No-Text Debounce (optional)
The system SHALL optionally support buffering messages without text content until text arrives.

#### Scenario: Media message without text
- **WHEN** a message contains only media (image, file, etc.) without text
- **AND** no_text_debounce_enabled is True
- **THEN** the system SHALL buffer the message
- **AND** start a shorter timer (no_text_max_wait_seconds, default 3 seconds)
- **AND** wait for a message with text from the same user

#### Scenario: Text message arrives after media
- **WHEN** a message with text arrives from the same user
- **AND** there are buffered no-text messages
- **THEN** the system SHALL merge the media placeholders with the text message
- **AND** the merged content SHALL be: "[图片]\n[文件]\n用户文字内容"

#### Scenario: No-text timeout
- **WHEN** no text message arrives within no_text_max_wait_seconds
- **THEN** the system SHALL process the buffered media messages alone
- **AND** create a default prompt: "[图片]" or "[文件: filename]"

#### Scenario: Disable no-text debounce
- **WHEN** no_text_debounce_enabled is False
- **THEN** media-only messages SHALL be processed immediately
- **AND** use the file name or media type as the content

### Requirement: should_debounce hook
The system SHALL support a configurable hook to determine whether to debounce a message.

#### Scenario: Use default should_debounce logic
- **WHEN** should_debounce_hook is not configured
- **THEN** the system SHALL use default logic:
  - System commands (starting with "/") SHALL NOT be debounced
  - Messages with [URGENT] tag SHALL NOT be debounced
  - All other messages SHALL be debounced

#### Scenario: Custom should_debounce hook
- **WHEN** should_debounce_hook is configured (e.g., "app.hooks.should_debounce")
- **THEN** the system SHALL call the custom function for each message
- **AND** if the function returns False, process immediately without debounce
- **AND** if the function returns True, apply normal debounce logic

#### Scenario: Hook execution error
- **WHEN** the should_debounce hook raises an exception
- **THEN** the system SHALL log the error
- **AND** fall back to default debounce logic
- **AND** process the message (not block)

### Requirement: Message merging strategy
The system SHALL merge buffered messages intelligently.

#### Scenario: Merge text messages
- **WHEN** multiple text messages are in the buffer
- **THEN** the system SHALL concatenate them with "\n\n" (double newline) separator
- **AND** preserve the order of arrival
- **AND** remove duplicate content (case-insensitive, within reason)

#### Scenario: Merge media placeholders
- **WHEN** the buffer contains media messages (image, file, etc.)
- **THEN** the system SHALL replace them with placeholders: "[图片]", "[文件: {filename}]"
- **AND** include the media metadata for later download
- **AND** preserve chronological order with text messages

#### Scenario: Mixed text and media
- **WHEN** the buffer contains both text and media messages
- **THEN** the system SHALL merge them in chronological order
- **AND** the merged text might look like: "问题1\n\n[图片]\n\n问题2"

#### Scenario: Batch draining optimization
- **WHEN** processing buffered messages
- **THEN** the system SHALL drain up to max_batch_size (default 10) messages at once
- **AND** only drain messages with the same debounce_key (same session)
- **AND** put back messages from other sessions

### Requirement: Session state management
The system SHALL maintain session state for each user-chat combination.

#### Scenario: State transitions
- **WHEN** transitioning between states
- **THEN** the state SHALL follow: idle → buffering → processing → idle
- **AND** state SHALL be stored in Redis with key: feishu:state:{open_id}:{chat_id}
- **AND** state SHALL have TTL of 300 seconds (5 minutes)
- **AND** on startup, orphaned "processing" states (older than 5 min) SHALL be reset to idle

#### Scenario: Concurrent session protection with lock renewal
- **WHEN** multiple Workers attempt to process messages for the same session
- **THEN** the system SHALL use Redis distributed lock (feishu:lock:{open_id}:{chat_id})
- **AND** lock SHALL have TTL equal to max processing time (300 seconds / 5 minutes)
- **AND** Worker SHALL renew/extend lock every 60 seconds during long processing (Watchdog)
- **AND** lock SHALL be released immediately after processing completes
- **AND** only one Worker SHALL process messages for a session at a time
- **AND** other Workers SHALL wait and retry with exponential backoff

### Requirement: Configurable debounce parameters
The system SHALL support configurable debounce parameters.

#### Scenario: Custom debounce wait time
- **WHEN** feishu_access_config.debounce.debounce_wait_seconds is configured
- **THEN** the system SHALL use the configured value (0.5-10 seconds)
- **AND** the default SHALL be 2 seconds if not configured

#### Scenario: Custom no-text debounce parameters
- **WHEN** feishu_access_config.debounce.no_text_debounce is configured
- **THEN** the system SHALL use:
  - no_text_debounce_enabled: true/false (default: true)
  - no_text_max_wait_seconds: 1-10 seconds (default: 3)

#### Scenario: Custom batch parameters
- **WHEN** feishu_access_config.debounce.max_batch_size is configured
- **THEN** the system SHALL use the configured value (1-50, default: 10)

#### Scenario: Disable debounce entirely
- **WHEN** debounce_wait_seconds is set to 0
- **THEN** the system SHALL process each message immediately without buffering
- **AND** should_debounce hook SHALL be ignored
- **AND** no_text_debounce SHALL be disabled
