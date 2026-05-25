using System.Text;
using System.Text.RegularExpressions;

namespace McGo.Client.Sync;

/// <summary>Server vs client built-in ignore behavior for <c>mods/</c> paths.</summary>
public enum IgnoreRole
{
    Server,
    Client,
}

/// <summary>Subset of .gitignore rules compatible with Python <c>ignore.py</c>.</summary>
public sealed class IgnoreRules
{
    private static readonly Dictionary<IgnoreRole, string> BuiltinPrefixes = new()
    {
        [IgnoreRole.Server] = "server-",
        [IgnoreRole.Client] = "client-",
    };

    private readonly List<(Regex Pattern, bool Negation)> _rules = new();
    private readonly IgnoreRole? _role;

    public IgnoreRules(string? ignoreFilePath, string baseDir, IgnoreRole? role = null)
    {
        _role = role;
        _ = baseDir; // reserved for future anchored rules
        if (string.IsNullOrEmpty(ignoreFilePath) || !File.Exists(ignoreFilePath))
            return;
        var lines = File.ReadAllLines(ignoreFilePath);
        foreach (var rawLine in lines)
        {
            var stripped = rawLine.TrimEnd('\r');
            if (string.IsNullOrWhiteSpace(stripped) || stripped.TrimStart().StartsWith("#", StringComparison.Ordinal))
                continue;

            var negation = false;
            var patternStr = stripped.TrimEnd();
            if (patternStr.StartsWith('!'))
            {
                negation = true;
                patternStr = patternStr[1..];
            }

            var dirOnly = false;
            if (patternStr.EndsWith('/'))
            {
                dirOnly = true;
                patternStr = patternStr[..^1];
            }

            if (string.IsNullOrEmpty(patternStr))
                continue;

            var regex = PatternToRegex(patternStr, dirOnly);
            _rules.Add((regex, negation));
        }
    }

    public bool IsIgnored(string relativePath, bool isDir = false)
    {
        var ignored = IsBuiltinIgnored(relativePath, isDir);
        foreach (var (regex, negation) in _rules)
        {
            if (regex.IsMatch(relativePath))
                ignored = !negation;
        }

        return ignored;
    }

    private bool IsBuiltinIgnored(string relativePath, bool isDir)
    {
        if (_role is null || isDir)
            return false;
        var parts = relativePath.Replace('\\', '/').Split('/');
        if (parts.Length < 2)
            return false;
        var dirParts = parts[..^1];
        if (!dirParts.Contains("mods", StringComparer.Ordinal))
            return false;
        var prefix = BuiltinPrefixes[_role.Value];
        return parts[^1].StartsWith(prefix, StringComparison.Ordinal);
    }

    private static Regex PatternToRegex(string pattern, bool dirOnly)
    {
        var anchored = pattern.StartsWith('/');
        if (anchored)
            pattern = pattern[1..];

        pattern = pattern.Replace('\\', '/');
        var parts = pattern.Split('/');

        var result = new StringBuilder();
        for (var i = 0; i < parts.Length; i++)
        {
            var part = parts[i];
            if (part == "**")
            {
                if (i == 0)
                    result.Append("(?:.*/)?");
                else if (i == parts.Length - 1)
                    result.Append("(?:/.*)?");
                else
                    result.Append("/(?:[^/]*/)*");
            }
            else
            {
                if (i > 0 && parts[i - 1] != "**")
                    result.Append('/');
                if (part.Contains("**", StringComparison.Ordinal))
                {
                    var escaped = Regex.Escape(part).Replace("\\*\\*", ".*", StringComparison.Ordinal);
                    result.Append(escaped);
                }
                else
                    result.Append(GlobToRegex(part));
            }
        }

        var fullPattern = result.ToString();
        if (pattern == "**")
            fullPattern = ".*";

        if (anchored)
            fullPattern = "^" + fullPattern;
        else
            fullPattern = "(?:^|.*/)" + fullPattern;

        fullPattern += dirOnly ? "(?:/.*)?$" : "$";
        return new Regex(fullPattern, RegexOptions.Compiled | RegexOptions.CultureInvariant);
    }

    private static string GlobToRegex(string glob)
    {
        var sb = new StringBuilder();
        var i = 0;
        while (i < glob.Length)
        {
            var c = glob[i];
            if (c == '*')
                sb.Append("[^/]*");
            else if (c == '?')
                sb.Append("[^/]");
            else if (c == '[')
            {
                var j = i + 1;
                if (j < glob.Length && glob[j] == ']')
                    j++;
                while (j < glob.Length && glob[j] != ']')
                    j++;
                if (j >= glob.Length)
                    sb.Append(Regex.Escape("["));
                else
                {
                    var bracket = glob.Substring(i, j - i + 1);
                    sb.Append(Regex.Escape(bracket).Replace("\\[", "[", StringComparison.Ordinal).Replace("\\]", "]", StringComparison.Ordinal));
                    i = j;
                }
            }
            else
                sb.Append(Regex.Escape(c.ToString()));
            i++;
        }

        return sb.ToString();
    }
}
