namespace WeaponDetection.Api.Contracts;

// ARCH-001 / ADR-009, IP-01 §11: the uniform response envelope for every /api/v1 endpoint —
// {success, message, data} on success, {success, message, errorCode} on failure. A single
// non-generic type (Data: object?) is used deliberately, rather than ApiResponse<T>, so the
// shared wrapping mechanism (ApiEnvelopeResultFilter) never needs reflection/generic-type
// construction to wrap an arbitrary controller-returned payload.
public sealed class ApiResponse
{
    public bool Success { get; }
    public string? Message { get; }
    public object? Data { get; }
    public string? ErrorCode { get; }

    private ApiResponse(bool success, string? message, object? data, string? errorCode)
    {
        Success = success;
        Message = message;
        Data = data;
        ErrorCode = errorCode;
    }

    public static ApiResponse Ok(object? data, string? message = null) => new(true, message, data, null);

    public static ApiResponse Fail(string errorCode, string message) => new(false, message, null, errorCode);
}
