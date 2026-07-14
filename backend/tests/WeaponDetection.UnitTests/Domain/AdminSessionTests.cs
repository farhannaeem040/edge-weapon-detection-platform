using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

public class AdminSessionTests
{
    private static readonly Guid SampleSessionId = Guid.NewGuid();
    private static readonly Guid SampleUserId = Guid.NewGuid();
    private static readonly DateTimeOffset IssuedAt = DateTimeOffset.UtcNow;

    [Fact]
    public void Constructor_WithValidValues_CreatesNonRevokedSession()
    {
        var session = new AdminSession(SampleSessionId, SampleUserId, IssuedAt, IssuedAt.AddHours(1));

        Assert.Equal(SampleSessionId, session.SessionId);
        Assert.Equal(SampleUserId, session.UserId);
        Assert.Equal(IssuedAt, session.IssuedAt);
        Assert.Equal(IssuedAt.AddHours(1), session.ExpiresAt);
        Assert.False(session.Revoked);
    }

    [Fact]
    public void Constructor_WithEmptySessionId_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new AdminSession(Guid.Empty, SampleUserId, IssuedAt, IssuedAt.AddHours(1)));
    }

    [Fact]
    public void Constructor_WithEmptyUserId_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new AdminSession(SampleSessionId, Guid.Empty, IssuedAt, IssuedAt.AddHours(1)));
    }

    [Fact]
    public void Constructor_WithExpiryEqualToIssuedAt_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new AdminSession(SampleSessionId, SampleUserId, IssuedAt, IssuedAt));
    }

    [Fact]
    public void Constructor_WithExpiryBeforeIssuedAt_Throws()
    {
        Assert.Throws<ArgumentException>(() =>
            new AdminSession(SampleSessionId, SampleUserId, IssuedAt, IssuedAt.AddMinutes(-1)));
    }

    [Fact]
    public void Revoke_SetsRevokedToTrue()
    {
        var session = new AdminSession(SampleSessionId, SampleUserId, IssuedAt, IssuedAt.AddHours(1));

        session.Revoke();

        Assert.True(session.Revoked);
    }

    [Fact]
    public void Revoke_CalledTwice_RemainsRevokedWithoutError()
    {
        var session = new AdminSession(SampleSessionId, SampleUserId, IssuedAt, IssuedAt.AddHours(1));

        session.Revoke();
        session.Revoke();

        Assert.True(session.Revoked);
    }
}
