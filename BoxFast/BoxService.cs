using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using Box.Automation.WSD;
using Box.V2;
using Box.V2.Models;

namespace BoxFast;

/// <summary>
/// One shared, authenticated BoxClient for every call (BoxAutomate.exe re-authenticated for each one), kept fresh in the
/// background, plus parallel byte-range downloads for big files. Port of the WPF Compiler's BoxService.
/// </summary>
public sealed class BoxService
{
    private const string UserToken = "8826064036";
    public const string DefaultFolderId = "368728134757";
    private const int MaxRetries = 3;
    private const int MaxParallelCalls = 16;
    private const int DownloadSegments = 8;
    private const long MinSegmentedSize = 4L * 1024 * 1024;
    private const int BufferSize = 1024 * 1024;
    private static readonly string[] ItemFields = ["name", "sha1", "etag", "sequence_id", "size"];
    // Only fetched when the caller asks ("dates": true): who uploaded a file and when (Dev Profile's activity calendar).
    private static readonly string[] ItemFieldsWithDates = ["name", "sha1", "etag", "sequence_id", "size", "created_at", "modified_at", "created_by", "modified_by"];

    public static BoxService Instance { get; } = new();

    private readonly SemaphoreSlim _gate = new(MaxParallelCalls);
    // Each tiny download is dominated by Box round-trip latency, not bandwidth: give them their own wide gate.
    private readonly SemaphoreSlim _smallGate = new(48);
    private readonly SemaphoreSlim _clientLock = new(1, 1);
    private BoxClient? _client;
    private DateTime _clientCreatedUtc;
    private bool _keepFreshStarted;

    private static readonly TimeSpan RenewEvery = TimeSpan.FromMinutes(20), HardExpiry = TimeSpan.FromMinutes(50);

    private static readonly HttpClient Http = new(new SocketsHttpHandler
    {
        MaxConnectionsPerServer = 64,
        AutomaticDecompression = DecompressionMethods.None,
        PooledConnectionLifetime = TimeSpan.FromMinutes(5),
        EnableMultipleHttp2Connections = true,
    })
    { Timeout = Timeout.InfiniteTimeSpan };

    private async Task<BoxClient> GetClientAsync(bool forceNew = false)
    {
        await _clientLock.WaitAsync();
        try
        {
            if (forceNew || _client is null || DateTime.UtcNow - _clientCreatedUtc > HardExpiry)
            {
                _client = await new BoxUserClient().GetUserBoxClient(UserToken);
                _clientCreatedUtc = DateTime.UtcNow;
            }
            return _client;
        }
        finally { _clientLock.Release(); }
    }

    /// <summary>Logs in to a NEW client without holding the lock (calls keep using the current one meanwhile), then swaps it in.</summary>
    private async Task RenewClientAsync()
    {
        var fresh = await new BoxUserClient().GetUserBoxClient(UserToken);
        await _clientLock.WaitAsync();
        try { _client = fresh; _clientCreatedUtc = DateTime.UtcNow; }
        finally { _clientLock.Release(); }
    }

    private void KeepFresh()
    {
        if (_keepFreshStarted) return;
        _keepFreshStarted = true;
        _ = Task.Run(async () =>
        {
            while (true)
            {
                await Task.Delay(RenewEvery);
                try { await RenewClientAsync(); } catch { /* the next real call retries with a new client */ }
            }
        });
    }

    /// <summary>Logs in at start so the first refresh/download does not pay for it, and keeps the login fresh.</summary>
    public async Task WarmUpAsync() { try { await GetClientAsync(); } catch { /* retried on first real call */ } KeepFresh(); }

    private async Task<T> WithRetryAsync<T>(Func<BoxClient, Task<T>> op)
    {
        Exception? last = null;
        for (var attempt = 1; attempt <= MaxRetries; attempt++)
        {
            await _gate.WaitAsync();
            try
            {
                var client = await GetClientAsync(forceNew: attempt > 1);
                return await op(client);
            }
            catch (Exception ex) { last = ex; }
            finally { _gate.Release(); }
            if (attempt < MaxRetries) await Task.Delay(800 * attempt);
        }
        throw new InvalidOperationException(last?.Message ?? "Box request failed", last);
    }

    // ── Small text files (Dev Profile's pinned message lives in one per developer) ────────────────────────────

    /// <summary>"canUpload": whether the account the app uses may add files to the folder (read-only question).</summary>
    public async Task<JsonObject> CanUploadAsync(string folderId)
    {
        var f = await WithRetryAsync(c => c.FoldersManager.GetInformationAsync(folderId, new[] { "name", "permissions" }));
        return new JsonObject { ["folder_id"] = folderId, ["name"] = f.Name, ["can_upload"] = f.Permissions?.CanUpload ?? false };
    }

    /// <summary>"readText": the content of a small text file.</summary>
    public async Task<JsonObject> ReadTextAsync(string fileId)
    {
        var text = await WithRetryAsync(async c =>
        {
            using var s = await c.FilesManager.DownloadAsync(fileId);
            using var r = new StreamReader(s, System.Text.Encoding.UTF8);
            return await r.ReadToEndAsync();
        });
        return new JsonObject { ["file_id"] = fileId, ["text"] = text };
    }

    /// <summary>"writeText": creates the file in the folder, or uploads a new version when a file with that name is already
    /// there (the file id stays the same and Box keeps the older versions).</summary>
    public Task<JsonObject> WriteTextAsync(string folderId, string name, string text, string? knownFileId = null) =>
        WriteBytesAsync(folderId, name, new System.Text.UTF8Encoding(false).GetBytes(text), knownFileId);

    /// <summary>"writeBytes": same as writeText for any content (Dev Profile photos).</summary>
    public async Task<JsonObject> WriteBytesAsync(string folderId, string name, byte[] bytes, string? knownFileId = null)
    {
        // The caller already knows the file (it saved it before): upload the new version directly - one Box call instead of
        // three. If that file is gone, fall through to the normal "find it or create it" path below.
        if (!string.IsNullOrEmpty(knownFileId))
        {
            try
            {
                var client = await GetClientAsync();
                using var direct = new MemoryStream(bytes);
                var f = await client.FilesManager.UploadNewVersionAsync(name, knownFileId, direct, fields: new List<string> { "name", "modified_at", "modified_by" });
                return new JsonObject { ["id"] = f.Id, ["name"] = f.Name, ["created"] = false, ["modified_at"] = f.ModifiedAt?.ToString("o"), ["modified_by"] = f.ModifiedBy?.Name };
            }
            catch { /* not there any more (or Box said no): the slow path decides */ }
        }
        var entries = await WithRetryAsync(async c =>
            (await c.FoldersManager.GetFolderItemsAsync(folderId, 1000, autoPaginate: true, fields: new[] { "name" })).Entries);
        var existing = entries.FirstOrDefault(e => e.Type == "file" && string.Equals(e.Name, name, StringComparison.OrdinalIgnoreCase));
        var file = await WithRetryAsync(async c =>
        {
            using var ms = new MemoryStream(bytes);
            if (existing is not null)
                return await c.FilesManager.UploadNewVersionAsync(name, existing.Id, ms, fields: new List<string> { "name", "modified_at", "modified_by" });
            var req = new BoxFileRequest { Name = name, Parent = new BoxRequestEntity { Id = folderId } };
            return await c.FilesManager.UploadAsync(req, ms, new List<string> { "name", "modified_at", "modified_by" });
        });
        return new JsonObject
        {
            ["id"] = file.Id, ["name"] = file.Name, ["created"] = existing is null,
            ["modified_at"] = file.ModifiedAt?.ToString("o"), ["modified_by"] = file.ModifiedBy?.Name,
        };
    }

    /// <summary>"ensureFolder": the id of the sub-folder *name* of *parentId*, creating it when it is not there yet.</summary>
    public async Task<JsonObject> EnsureFolderAsync(string parentId, string name)
    {
        var entries = await WithRetryAsync(async c =>
            (await c.FoldersManager.GetFolderItemsAsync(parentId, 1000, autoPaginate: true, fields: new[] { "name" })).Entries);
        var existing = entries.FirstOrDefault(e => e.Type == "folder" && string.Equals(e.Name, name, StringComparison.OrdinalIgnoreCase));
        if (existing is not null) return new JsonObject { ["id"] = existing.Id, ["name"] = existing.Name, ["created"] = false };
        var folder = await WithRetryAsync(c => c.FoldersManager.CreateAsync(
            new BoxFolderRequest { Name = name, Parent = new BoxRequestEntity { Id = parentId } }));
        return new JsonObject { ["id"] = folder.Id, ["name"] = folder.Name, ["created"] = true };
    }

    /// <summary>"deleteFolder": removes a folder with everything in it (only used to clean up the self-test).</summary>
    public async Task<JsonObject> DeleteFolderAsync(string folderId)
    {
        await WithRetryAsync(async c => { await c.FoldersManager.DeleteAsync(folderId, recursive: true); return true; });
        return new JsonObject { ["deleted"] = folderId };
    }

    /// <summary>"deleteFile": removes a file (used to clean up the self-test file).</summary>
    public async Task<JsonObject> DeleteFileAsync(string fileId)
    {
        await WithRetryAsync(async c => { await c.FilesManager.DeleteAsync(fileId); return true; });
        return new JsonObject { ["deleted"] = fileId };
    }

    // ── Listing ──────────────────────────────────────────────────────────────────────────────────

    /// <summary>"list": {folder_id,total_items,items[{type,id,name,sha1?}],summary,status}.</summary>
    public async Task<JsonObject> ListFolderAsync(string folderId, bool dates = false)
    {
        var fields = dates ? ItemFieldsWithDates : ItemFields;
        var entries = await WithRetryAsync(async c =>
            (await c.FoldersManager.GetFolderItemsAsync(folderId, 100, autoPaginate: true, fields: fields)).Entries);

        var items = new JsonArray();
        int files = 0, folders = 0;
        foreach (var e in entries)
        {
            if (string.IsNullOrEmpty(e.Type) || string.IsNullOrEmpty(e.Id)) continue;
            if (e.Type == "file") files++;
            else if (e.Type == "folder") folders++;
            var li = new JsonObject { ["type"] = e.Type, ["id"] = e.Id, ["name"] = e.Name };
            if (e is BoxFile bf && !string.IsNullOrEmpty(bf.Sha1)) li["sha1"] = bf.Sha1;
            if (dates && e is BoxFile df)
            {
                if (df.CreatedAt != null) li["created_at"] = df.CreatedAt.Value.ToString("o");
                if (df.ModifiedAt != null) li["modified_at"] = df.ModifiedAt.Value.ToString("o");
                if (!string.IsNullOrEmpty(df.CreatedBy?.Name)) li["created_by"] = df.CreatedBy!.Name;
                if (!string.IsNullOrEmpty(df.ModifiedBy?.Name)) li["modified_by"] = df.ModifiedBy!.Name;
            }
            items.Add(li);
        }
        return new JsonObject
        {
            ["folder_id"] = folderId,
            ["total_items"] = entries.Count,
            ["items"] = items,
            ["summary"] = new JsonObject { ["files"] = files, ["folders"] = folders },
            ["status"] = "SUCCESS",
        };
    }

    /// <summary>"getInfo": {folder_id,item_count,items[{name,type,id,size,...}],status}.</summary>
    public async Task<JsonObject> GetInfoAsync(string folderId)
    {
        var entries = await WithRetryAsync(async c =>
            (await c.FoldersManager.GetFolderItemsAsync(folderId, 100, autoPaginate: true)).Entries);

        var items = new JsonArray();
        foreach (var e in entries)
        {
            string? createdAt = null, modifiedAt = null, createdBy = null, modifiedBy = null;
            if (e is BoxFile f)
            {
                createdAt = f.CreatedAt?.ToString("o"); modifiedAt = f.ModifiedAt?.ToString("o");
                createdBy = f.CreatedBy?.Name; modifiedBy = f.ModifiedBy?.Name;
            }
            else if (e is BoxFolder d)
            {
                createdAt = d.CreatedAt?.ToString("o"); modifiedAt = d.ModifiedAt?.ToString("o");
                createdBy = d.CreatedBy?.Name; modifiedBy = d.ModifiedBy?.Name;
            }
            items.Add(new JsonObject
            {
                ["name"] = e.Name ?? "",
                ["type"] = e.Type,
                ["id"] = e.Id,
                ["size"] = e.Size?.ToString(),
                ["etag"] = e.ETag ?? "N/A",
                ["sequence_id"] = e.SequenceId ?? "N/A",
                ["created_at"] = createdAt,
                ["modified_at"] = modifiedAt,
                ["created_by"] = createdBy,
                ["modified_by"] = modifiedBy,
            });
        }
        return new JsonObject
        {
            ["folder_id"] = folderId,
            ["item_count"] = items.Count,
            ["items"] = items,
            ["status"] = "SUCCESS",
        };
    }

    // ── Download ─────────────────────────────────────────────────────────────────────────────────

    private static bool SameAsBox(string path, string? boxSha1)
    {
        if (string.IsNullOrEmpty(boxSha1) || !File.Exists(path)) return false;
        try
        {
            using var fs = File.OpenRead(path);
            using var sha = SHA1.Create();
            return Convert.ToHexString(sha.ComputeHash(fs)).Equals(boxSha1, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    /// <summary>
    /// Downloads <paramref name="fileId"/> into <paramref name="destDir"/> under its Box name (like BoxAutomate.exe's
    /// "download"). Big files pull up to 8 byte-range segments in parallel. If <paramref name="sha1"/> matches the file already
    /// on disk nothing is downloaded. Returns (path, unchanged).
    /// </summary>
    public async Task<(string path, bool unchanged)> DownloadAsync(string fileId, string destDir, string? sha1, Action<long, long>? onProgress = null)
    {
        Directory.CreateDirectory(destDir);
        Exception? last = null;
        for (var attempt = 1; attempt <= MaxRetries; attempt++)
        {
            string? tmp = null;
            try
            {
                var (url, size, name, boxSha) = await ResolveAsync(fileId, attempt > 1);
                var dest = Path.Combine(destDir, name);
                if (SameAsBox(dest, sha1 ?? boxSha)) return (dest, true);
                tmp = dest + ".part";
                await FetchAsync(fileId, url, size, tmp, attempt > 1, onProgress);
                File.Move(tmp, dest, true);
                return (dest, false);
            }
            catch (Exception ex)
            {
                last = ex;
                if (attempt < MaxRetries) await Task.Delay(800 * attempt);
            }
            finally { try { if (tmp is not null && File.Exists(tmp)) File.Delete(tmp); } catch { /* ignore */ } }
        }
        throw new InvalidOperationException(last?.Message ?? "Download failed", last);
    }

    private async Task<(Uri? url, long size, string name, string? sha1)> ResolveAsync(string fileId, bool forceNewClient)
    {
        await _gate.WaitAsync();
        try
        {
            var client = await GetClientAsync(forceNewClient);
            // link + info resolved in parallel (one round trip instead of two)
            var uriTask = client.FilesManager.GetDownloadUriAsync(fileId);
            var infoTask = client.FilesManager.GetInformationAsync(fileId, new[] { "name", "size", "sha1" });
            Uri? url = null;
            try { url = await uriTask; } catch { /* SDK stream fallback in FetchAsync */ }
            var info = await infoTask;
            return (url, info.Size ?? 0, info.Name, info.Sha1);
        }
        finally { _gate.Release(); }
    }

    private async Task FetchAsync(string fileId, Uri? url, long size, string tmp, bool forceNewClient, Action<long, long>? onProgress)
    {
        if (url is null)
        {
            var client = await GetClientAsync(forceNewClient);
            using var s = await client.FilesManager.DownloadAsync(fileId);
            await using var fs = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, BufferSize, FileOptions.SequentialScan);
            await CopyWithProgressAsync(s, fs, size, onProgress);
            return;
        }
        // Small files: one stream (segmenting only adds round trips). Big files: parallel ranges.
        if (size >= MinSegmentedSize)
        {
            try { await DownloadSegmentedAsync(url, size, tmp, onProgress); return; }
            catch (RangeNotSupportedException) { /* fall through to a single stream */ }
        }
        using var resp = await Http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead);
        resp.EnsureSuccessStatusCode();
        await using var body = await resp.Content.ReadAsStreamAsync();
        await using var fo = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, BufferSize, FileOptions.SequentialScan);
        await CopyWithProgressAsync(body, fo, size, onProgress);
    }

    private static async Task CopyWithProgressAsync(Stream src, Stream dst, long total, Action<long, long>? onProgress)
    {
        var buf = new byte[BufferSize];
        long done = 0; int n;
        while ((n = await src.ReadAsync(buf)) > 0)
        {
            await dst.WriteAsync(buf.AsMemory(0, n));
            done += n;
            onProgress?.Invoke(done, total);
        }
    }

    private static async Task DownloadSegmentedAsync(Uri url, long size, string tmp, Action<long, long>? onProgress)
    {
        await using (var create = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
            create.SetLength(size);

        var segCount = (int)Math.Min(DownloadSegments, Math.Max(2, size / (2L * 1024 * 1024)));
        var segSize = (size + segCount - 1) / segCount;
        long done = 0;

        await using var handle = new FileStream(tmp, FileMode.Open, FileAccess.Write, FileShare.ReadWrite, 1, FileOptions.Asynchronous);
        await Task.WhenAll(Enumerable.Range(0, segCount).Select(async i =>
        {
            var start = i * segSize;
            var end = Math.Min(start + segSize - 1, size - 1);
            if (start <= end) await DownloadSegmentAsync(url, handle.SafeFileHandle, start, end, n => onProgress?.Invoke(Interlocked.Add(ref done, n), size));
        }));
    }

    private static async Task DownloadSegmentAsync(Uri url, Microsoft.Win32.SafeHandles.SafeFileHandle file, long start, long end, Action<long> onBytes)
    {
        var pos = start;
        for (var attempt = 1; ; attempt++)
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, url);
                req.Headers.Range = new RangeHeaderValue(pos, end);
                using var resp = await Http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead);
                if (resp.StatusCode != HttpStatusCode.PartialContent) { resp.EnsureSuccessStatusCode(); throw new RangeNotSupportedException(); }
                await using var s = await resp.Content.ReadAsStreamAsync();
                var buf = new byte[256 * 1024];
                int n;
                while ((n = await s.ReadAsync(buf)) > 0)
                {
                    await RandomAccess.WriteAsync(file, buf.AsMemory(0, n), pos);
                    pos += n;
                    onBytes(n);
                }
                if (pos > end) return;
                throw new IOException("Segment ended early");
            }
            catch (Exception ex) when (ex is not RangeNotSupportedException && attempt < 4)
            {
                await Task.Delay(500 * attempt);
            }
        }
    }

    /// <summary>
    /// Fastest way to fetch a tiny file (icon, Flow.txt, README): ONE call through the SDK stream, no size/link lookup.
    /// The caller already knows the file name from the folder listing.
    /// </summary>
    public async Task<(string path, bool unchanged)> DownloadSmallAsync(string fileId, string destDir, string name, string? sha1)
    {
        Directory.CreateDirectory(destDir);
        var dest = Path.Combine(destDir, name);
        if (SameAsBox(dest, sha1)) return (dest, true);
        var tmp = dest + ".part";
        await _smallGate.WaitAsync();
        try
        {
            Exception? last = null;
            for (var attempt = 1; attempt <= MaxRetries; attempt++)
            {
                try
                {
                    var client = await GetClientAsync(forceNew: attempt > 1);
                    using var s = await client.FilesManager.DownloadAsync(fileId);
                    await using (var fs = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.SequentialScan))
                        await s.CopyToAsync(fs);
                    last = null; break;
                }
                catch (Exception ex)
                {
                    last = ex;
                    if (attempt < MaxRetries) await Task.Delay(500 * attempt);
                }
            }
            if (last is not null) throw new InvalidOperationException(last.Message, last);
            File.Move(tmp, dest, true);
            return (dest, false);
        }
        finally
        {
            _smallGate.Release();
            try { if (File.Exists(tmp)) File.Delete(tmp); } catch { /* ignore */ }
        }
    }
}

internal sealed class RangeNotSupportedException : Exception { }
