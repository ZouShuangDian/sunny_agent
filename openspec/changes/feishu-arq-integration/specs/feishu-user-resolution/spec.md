## ADDED Requirements

### Requirement: Resolve user by open_id
The system SHALL resolve system user information from Feishu open_id.

#### Scenario: Successful user resolution
- **WHEN** processing a message from open_id "ou_xxx"
- **THEN** the system SHALL call Feishu API GET /contact/v3/users/batch
- **AND** extract employee_no from the response
- **AND** query the users table to find matching usernumb
- **AND** return the user_id (UUID) and usernumb

#### Scenario: User not found in system
- **WHEN** the employee_no from Feishu does not exist in the users table
- **THEN** the system SHALL reject the message
- **AND** send an error message to the user: "您的账号未在系统中注册，请联系管理员"
- **AND** log the event

#### Scenario: Feishu API failure
- **WHEN** the Feishu API call fails (network error, rate limit, etc.)
- **THEN** the system SHALL retry up to 3 times with exponential backoff
- **AND** if still failing, reject the message and log the error

### Requirement: Token caching
The system SHALL cache Feishu access tokens to reduce API calls.

#### Scenario: Token cache hit
- **WHEN** a token is needed and it exists in Redis with key "feishu:token:{app_id}"
- **AND** the TTL has not expired (TTL < 7000s)
- **THEN** the system SHALL use the cached token

#### Scenario: Token cache miss
- **WHEN** a token is needed and it does not exist in Redis or has expired
- **THEN** the system SHALL call Feishu API POST /auth/v3/tenant_access_token/internal
- **AND** cache the new token in Redis with TTL 7000 seconds
- **AND** return the new token

#### Scenario: Token refresh failure
- **WHEN** token refresh fails after 3 retries
- **THEN** the system SHALL reject the message
- **AND** log a critical error
- **AND** trigger an alert

### Requirement: User binding management
The system SHALL maintain user binding records in feishu_user_bindings table.

#### Scenario: New user binding created
- **WHEN** a valid user is resolved for the first time
- **THEN** the system SHALL create a record in feishu_user_bindings
- **AND** set binding_status to "approved"
- **AND** record open_id, union_id, employee_no, usernumb, and user_id

#### Scenario: Existing binding updated
- **WHEN** a user is resolved and a binding record already exists
- **THEN** the system SHALL update the last_accessed_at timestamp
- **AND** verify that employee_no and usernumb match

### Requirement: Multi-tenant support
The system SHALL support multiple Feishu applications.

#### Scenario: Multiple app_id configuration
- **WHEN** configurations exist for multiple app_ids in feishu_access_config
- **THEN** the system SHALL use the correct app credentials based on the message's app_id
- **AND** maintain separate token caches for each app_id
