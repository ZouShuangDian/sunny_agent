## ADDED Requirements

### Requirement: Receive Feishu webhook events
The system SHALL receive and validate webhook events from Feishu Open Platform.

#### Scenario: Valid message event received
- **WHEN** Feishu platform sends a valid `im.message.receive_v1` event to the webhook endpoint
- **THEN** the system SHALL verify the request signature using the configured encrypt_key
- **AND** decrypt the payload if encrypted
- **AND** push the event to Redis List `feishu:webhook:queue`
- **AND** return HTTP 200 OK immediately

#### Scenario: Invalid signature
- **WHEN** a request is received with invalid X-Signature header
- **THEN** the system SHALL return HTTP 401 Unauthorized
- **AND** log the security event

#### Scenario: URL verification challenge
- **WHEN** Feishu sends a URL verification request (type: url_verification)
- **THEN** the system SHALL verify the token matches the configured verification_token
- **AND** return the challenge value in the response

### Requirement: Handle encrypted messages
The system SHALL decrypt messages encrypted by Feishu platform.

#### Scenario: Encrypted message received
- **WHEN** a message contains an `encrypt` field
- **THEN** the system SHALL decrypt it using AES-256-CBC with the configured encrypt_key
- **AND** proceed with the decrypted payload

### Requirement: Message deduplication
The system SHALL prevent processing duplicate messages using nonce tracking.

#### Scenario: Duplicate message received
- **WHEN** a message with a previously processed nonce (X-Nonce header) is received
- **THEN** the system SHALL reject the request with HTTP 401
- **AND** log a potential replay attack

### Requirement: Message handoff to ARQ
The system SHALL push messages to a temporary Redis List for subsequent transfer to ARQ queue.

#### Scenario: Message queued for ARQ transfer
- **WHEN** a message is successfully validated and decrypted
- **THEN** the system SHALL push it to Redis List `feishu:webhook:queue`
- **AND** the message SHALL be picked up by `message_transfer_loop` BRPOPLPUSH long-running task (started in Worker startup)
- **AND** the long-running task SHALL atomically transfer it to ARQ Queue `arq:feishu:queue` for unified processing
- **AND** the HTTP 200 response SHALL be returned immediately (non-blocking)

### Requirement: Queue management
The system SHALL handle queue overflow gracefully.

#### Scenario: Queue is full
- **WHEN** the queue length exceeds the configured limit (default 10000)
- **THEN** the system SHALL reject new messages with HTTP 503
- **AND** log a warning about queue overflow
