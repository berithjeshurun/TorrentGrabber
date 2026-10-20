# @torrentgrabber/engine-sdk

```bash
npm install ./sdk/javascript
# or from repo root:
npm install file:sdk/javascript
```

```js
import { TorrentEngine } from "@torrentgrabber/engine-sdk";

const eng = new TorrentEngine("http://127.0.0.1:17890");
console.log(await eng.health());
const job = await eng.add("magnet:?xt=urn:btih:…", { mode: "stream" });
console.log(eng.mediaUrl(job.id));
```

Works in Node 18+ and browsers (CORS only if you change engine bind — default is localhost).
