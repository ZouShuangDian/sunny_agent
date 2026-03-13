## ADDED Requirements

### Requirement: DM access control policy
The system SHALL enforce access control policies for direct messages based on configuration.

#### Scenario: DM policy is "open"
- **WHEN** a direct message is received and dm_policy is set to "open"
- **THEN** the system SHALL allow the message to be processed

#### Scenario: DM policy is "allowlist" and user is allowed
- **WHEN** a direct message is received from a user whose employee_no is in dm_allowlist
- **THEN** the system SHALL allow the message to be processed

#### Scenario: DM policy is "allowlist" and user is not allowed
- **WHEN** a direct message is received from a user not in dm_allowlist
- **THEN** the system SHALL reject the message
- **AND** send a rejection message to the user: "您没有权限使用此服务"

#### Scenario: DM policy is "disabled"
- **WHEN** a direct message is received and dm_policy is set to "disabled"
- **THEN** the system SHALL reject the message silently (no response)

### Requirement: Group access control policy
The system SHALL enforce access control policies for group messages.

#### Scenario: Group policy is "open"
- **WHEN** a group message is received and group_policy is set to "open"
- **THEN** the system SHALL allow the message to be processed

#### Scenario: Group policy is "allowlist" and group is allowed
- **WHEN** a group message is received from a chat_id in group_allowlist
- **THEN** the system SHALL allow the message to be processed

#### Scenario: Group policy is "allowlist" and group is not allowed
- **WHEN** a group message is received from a chat_id not in group_allowlist
- **THEN** the system SHALL reject the message silently

#### Scenario: Group policy is "disabled"
- **WHEN** a group message is received and group_policy is set to "disabled"
- **THEN** the system SHALL reject the message silently

### Requirement: Require mention in groups
The system SHALL enforce @mention requirements for group messages.

#### Scenario: require_mention is true and bot is mentioned
- **WHEN** a group message is received with the bot mentioned in the mentions array
- **AND** the group configuration has require_mention set to true
- **THEN** the system SHALL allow the message to be processed

#### Scenario: require_mention is true and bot is not mentioned
- **WHEN** a group message is received without mentioning the bot
- **AND** the group configuration has require_mention set to true
- **THEN** the system SHALL reject the message silently

#### Scenario: require_mention is false
- **WHEN** a group message is received
- **AND** the group configuration has require_mention set to false
- **THEN** the system SHALL allow the message regardless of mention status

### Requirement: Configuration management
The system SHALL load access control configuration from PostgreSQL.

#### Scenario: Configuration loaded at startup
- **WHEN** the Worker starts
- **THEN** the system SHALL load all active configurations from feishu_access_config table
- **AND** cache them in memory for fast access

#### Scenario: Configuration reload
- **WHEN** an administrator updates the configuration in the database
- **THEN** the system SHALL reload the configuration within 60 seconds
- **OR** provide an API endpoint to trigger immediate reload
