using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using BoxFast;

// BoxFast: long-lived Box helper. One JSON request per stdin line, one JSON response per stdout line; requests run concurrently,
// so responses can come back out of order (match them by "id"). EOF on stdin = the Python app is gone = exit.
//   {"id":1,"cmd":"ping"}
//   {"id":2,"cmd":"getInfoDefault"} | {"cmd":"getInfo","folderId":".."} | {"cmd":"list","folderId":"..","dates":true}
//   {"cmd":"canUpload","folderId":".."} | {"cmd":"readText","fileId":".."} | {"cmd":"writeText","folderId":"..","name":"..","text":".."} | {"cmd":"deleteFile","fileId":".."}
//   {"cmd":"writeBytes","folderId":"..","name":"..","data":"<base64>"} | {"cmd":"ensureFolder","parentId":"..","name":".."} | {"cmd":"deleteFolder","folderId":".."}
//   {"id":3,"cmd":"download","fileId":"..","dest":"<dir>","sha1":"<optional>"}
//   {"id":4,"cmd":"downloadSmall","fileId":"..","dest":"<dir>","name":"Flow.txt","sha1":"<optional>"}
//   -> {"id":N,"ok":true,"data":{...}}  or  {"id":N,"ok":false,"error":"..."}
var utf8 = new UTF8Encoding(false);
using var stdin = new StreamReader(Console.OpenStandardInput(), utf8);
using var stdout = new StreamWriter(Console.OpenStandardOutput(), utf8, 64 * 1024) { AutoFlush = true, NewLine = "\n" };
var outLock = new object();

void Reply(JsonNode? id, bool ok, JsonNode? data, string? error)
{
    var r = new JsonObject { ["id"] = id is null ? null : JsonNode.Parse(id.ToJsonString()), ["ok"] = ok };
    if (data is not null) r["data"] = data;
    if (error is not null) r["error"] = error;
    var line = r.ToJsonString(new JsonSerializerOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
    lock (outLock) stdout.WriteLine(line);
}

static string? Str(JsonObject o, string key) => o[key]?.GetValue<string>();

async Task HandleAsync(JsonObject req)
{
    var id = req["id"];
    try
    {
        var box = BoxService.Instance;
        var cmd = (Str(req, "cmd") ?? "").ToLowerInvariant();
        switch (cmd)
        {
            case "ping":
                Reply(id, true, new JsonObject { ["pong"] = true }, null);
                break;
            case "warmup":
                await box.WarmUpAsync();
                Reply(id, true, new JsonObject { ["warm"] = true }, null);
                break;
            case "getinfodefault":
                Reply(id, true, await box.GetInfoAsync(BoxService.DefaultFolderId), null);
                break;
            case "getinfo":
                Reply(id, true, await box.GetInfoAsync(Str(req, "folderId") ?? throw new ArgumentException("folderId missing")), null);
                break;
            case "list":
                // "dates":true adds created_at/modified_at/created_by/modified_by to each file; without it the reply is unchanged.
                Reply(id, true, await box.ListFolderAsync(Str(req, "folderId") ?? throw new ArgumentException("folderId missing"),
                                                          req["dates"]?.GetValue<bool>() == true), null);
                break;
            case "canupload":
                Reply(id, true, await box.CanUploadAsync(Str(req, "folderId") ?? throw new ArgumentException("folderId missing")), null);
                break;
            case "readtext":
                Reply(id, true, await box.ReadTextAsync(Str(req, "fileId") ?? throw new ArgumentException("fileId missing")), null);
                break;
            case "writetext":
                Reply(id, true, await box.WriteTextAsync(Str(req, "folderId") ?? throw new ArgumentException("folderId missing"),
                                                        Str(req, "name") ?? throw new ArgumentException("name missing"),
                                                        Str(req, "text") ?? "", Str(req, "fileId")), null);
                break;
            case "writebytes":
                Reply(id, true, await box.WriteBytesAsync(Str(req, "folderId") ?? throw new ArgumentException("folderId missing"),
                                                         Str(req, "name") ?? throw new ArgumentException("name missing"),
                                                         Convert.FromBase64String(Str(req, "data") ?? ""), Str(req, "fileId")), null);
                break;
            case "ensurefolder":
                Reply(id, true, await box.EnsureFolderAsync(Str(req, "parentId") ?? throw new ArgumentException("parentId missing"),
                                                           Str(req, "name") ?? throw new ArgumentException("name missing")), null);
                break;
            case "deletefolder":
                Reply(id, true, await box.DeleteFolderAsync(Str(req, "folderId") ?? throw new ArgumentException("folderId missing")), null);
                break;
            case "deletefile":
                Reply(id, true, await box.DeleteFileAsync(Str(req, "fileId") ?? throw new ArgumentException("fileId missing")), null);
                break;
            case "download":
            {
                // Optional live progress: {"id":N,"progress":{"done":..,"total":..}} lines, at most ~7 per second.
                Action<long, long>? onProgress = null;
                if (req["progress"]?.GetValue<bool>() == true)
                {
                    var clock = System.Diagnostics.Stopwatch.StartNew();
                    long lastMs = -1000;
                    onProgress = (done, total) =>
                    {
                        var now = clock.ElapsedMilliseconds;
                        if (now - lastMs < 150 && done < total) return;
                        lastMs = now;
                        var line = new JsonObject { ["id"] = id is null ? null : JsonNode.Parse(id.ToJsonString()),
                            ["progress"] = new JsonObject { ["done"] = done, ["total"] = total } }.ToJsonString();
                        lock (outLock) stdout.WriteLine(line);
                    };
                }
                var (path, same) = await box.DownloadAsync(Str(req, "fileId") ?? throw new ArgumentException("fileId missing"),
                    Str(req, "dest") ?? throw new ArgumentException("dest missing"), Str(req, "sha1"), onProgress);
                Reply(id, true, new JsonObject { ["path"] = path, ["unchanged"] = same }, null);
                break;
            }
            case "downloadsmall":
            {
                var (path, same) = await box.DownloadSmallAsync(Str(req, "fileId") ?? throw new ArgumentException("fileId missing"),
                    Str(req, "dest") ?? throw new ArgumentException("dest missing"),
                    Str(req, "name") ?? throw new ArgumentException("name missing"), Str(req, "sha1"));
                Reply(id, true, new JsonObject { ["path"] = path, ["unchanged"] = same }, null);
                break;
            }
            default:
                Reply(id, false, null, $"Unknown command '{cmd}'");
                break;
        }
    }
    catch (Exception ex) { Reply(id, false, null, ex.GetBaseException().Message); }
}

// Announce readiness, and start the Box login right away so the first real request does not wait for it.
Reply(null, true, new JsonObject { ["ready"] = true }, null);
_ = Task.Run(() => BoxService.Instance.WarmUpAsync());

string? input;
while ((input = await stdin.ReadLineAsync()) is not null)
{
    if (input.Length == 0) continue;
    JsonObject? req;
    try { req = JsonNode.Parse(input) as JsonObject; } catch { req = null; }
    if (req is null) { Reply(null, false, null, "Invalid request"); continue; }
    _ = Task.Run(() => HandleAsync(req));
}
