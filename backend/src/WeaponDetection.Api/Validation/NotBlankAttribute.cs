using System.ComponentModel.DataAnnotations;

namespace WeaponDetection.Api.Validation;

// [Required] alone accepts a whitespace-only string (it only rejects null/empty). FS-01 §11
// requires blank credential/password input to be rejected the same as missing input, so this
// attribute treats null, empty, and whitespace-only strings identically.
[AttributeUsage(AttributeTargets.Property | AttributeTargets.Field | AttributeTargets.Parameter)]
public sealed class NotBlankAttribute : ValidationAttribute
{
    public NotBlankAttribute()
        : base("The {0} field is required.")
    {
    }

    public override bool IsValid(object? value) => value is string s && !string.IsNullOrWhiteSpace(s);
}
