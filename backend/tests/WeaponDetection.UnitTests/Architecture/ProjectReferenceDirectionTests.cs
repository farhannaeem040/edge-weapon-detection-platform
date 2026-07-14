using System;
using System.IO;
using System.Linq;
using Xunit;

namespace WeaponDetection.UnitTests.Architecture;

// Guards the Clean Architecture dependency direction established in IP-01 §2, by inspecting the
// .csproj files directly. Reflection over compiled assemblies is not reliable here: an assembly
// with no code that actually uses a referenced project's types can end up with no corresponding
// AssemblyRef entry at all, even though the ProjectReference exists in the .csproj — Domain and
// Application do not yet contain any entities (they are added in later tasks), so this file-based
// check is the correct level at which to enforce the dependency direction today.
public class ProjectReferenceDirectionTests
{
    private static readonly string SrcDirectory = FindSrcDirectory();

    [Fact]
    public void Domain_DoesNotReference_Infrastructure()
    {
        Assert.DoesNotContain("WeaponDetection.Infrastructure", ProjectReferencesOf("WeaponDetection.Domain"));
    }

    [Fact]
    public void Domain_HasNoProjectReferences()
    {
        Assert.Empty(ProjectReferencesOf("WeaponDetection.Domain"));
    }

    [Fact]
    public void Application_DoesNotReference_Infrastructure()
    {
        Assert.DoesNotContain("WeaponDetection.Infrastructure", ProjectReferencesOf("WeaponDetection.Application"));
    }

    [Fact]
    public void Application_References_Domain()
    {
        Assert.Contains("WeaponDetection.Domain", ProjectReferencesOf("WeaponDetection.Application"));
    }

    [Fact]
    public void Infrastructure_References_Application()
    {
        Assert.Contains("WeaponDetection.Application", ProjectReferencesOf("WeaponDetection.Infrastructure"));
    }

    private static string[] ProjectReferencesOf(string projectName)
    {
        var csprojPath = Path.Combine(SrcDirectory, projectName, $"{projectName}.csproj");
        var content = File.ReadAllText(csprojPath);

        return content
            .Split('\n')
            .Where(line => line.Contains("ProjectReference"))
            .Select(line =>
            {
                var start = line.IndexOf("Include=\"", StringComparison.Ordinal) + "Include=\"".Length;
                var end = line.IndexOf('"', start);
                var path = line[start..end];
                return Path.GetFileNameWithoutExtension(path);
            })
            .ToArray();
    }

    private static string FindSrcDirectory()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "src")))
        {
            dir = dir.Parent;
        }

        if (dir is null)
        {
            throw new InvalidOperationException("Could not locate the backend 'src' directory from the test output path.");
        }

        return Path.Combine(dir.FullName, "src");
    }
}
