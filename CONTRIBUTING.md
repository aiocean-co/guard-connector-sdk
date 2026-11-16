# Contributing

Fork the repository, branch from `main`, and open a pull request. Describe the source you support, the event fields and permissions it requires, and how you verified the change. Contributions are made under [MPL-2.0](LICENSE). Submit code and fixtures you have the right to share.

Keep provider records in the adapter. Submit only fields allowed by the public observation contract. Use synthetic test records and the local receiver in tests. Test both the successful mapping and at least one invalid or incomplete source record. Add documentation for a new adapter's input format and declared coverage.

```sh
python3 -m unittest discover -s tests -v
```

Maintainers review changes and publish official versions. [Security reporting](SECURITY.md)
