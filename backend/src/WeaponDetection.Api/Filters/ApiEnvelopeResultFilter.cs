using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using WeaponDetection.Api.Contracts;

namespace WeaponDetection.Api.Filters;

// IP-01 §11/T-09: wraps every successful (2xx) controller response in the uniform ApiResponse
// envelope automatically, so individual controllers never hand-build it (ADR-009). Registered
// once in Program.cs and reused by all controllers, present and future. A result that already
// carries an ApiResponse (e.g. a controller-constructed 401 failure, which is inherently
// endpoint-specific and cannot be inferred generically here) is left untouched.
public sealed class ApiEnvelopeResultFilter : IAsyncResultFilter
{
    public async Task OnResultExecutionAsync(ResultExecutingContext context, ResultExecutionDelegate next)
    {
        if (context.Result is ObjectResult { Value: not ApiResponse } objectResult)
        {
            var statusCode = objectResult.StatusCode ?? StatusCodes.Status200OK;
            if (statusCode is >= 200 and < 300)
            {
                objectResult.Value = ApiResponse.Ok(objectResult.Value);
            }
        }

        await next();
    }
}
