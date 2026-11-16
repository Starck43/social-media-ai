"""Default-off customer communications transports (P15-T06).

Internal adapter API only, not a permission/approval or queue entrypoint. The
caller must resolve fresh owned accounts/recipient evidence and durably fence a
reviewed send before dispatch. No existing notification/digest path uses this API.
"""
