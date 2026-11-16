# Security

Use GitHub **Security → Report a vulnerability** for private reports once private reporting is enabled. Include the affected version, a short description, and steps to reproduce using synthetic data. For a repository without private reporting, request a private channel from a maintainer and keep the public issue to the request for contact.

Configure each connector with a trusted HTTPS intake URL and a dedicated `events:ingest` key from the intended organization and unit. Select only that scope for the connector. Use the test receiver on loopback with a disposable token. Keep test credentials, logs containing raw source records, and customer data out of issues, pull requests, fixtures and release archives.
