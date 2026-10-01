const state = { snapshot: null, selected: null, pausedFrames: [] }

function root() {
  let path = window.location.pathname
  if (!path.endsWith("/")) {
    path += "/"
  }
  return new URL(path, window.location.origin)
}

function api(path) {
  return new URL(String(path).replace(/^\//, ""), root())
}

async function request(path, options) {
  const response = await fetch(api(path), options)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(body.detail || response.statusText)
  }
  return body
}

function tone(text) {
  const value = (text || "").toLowerCase()
  if (value.startsWith("ok") || value.startsWith("operational")) return "ok"
  if (value.includes("warning") || value.includes("maintenance") || value.includes("initialization")) return "warning"
  if (value.includes("error") || value.includes("critical") || value.includes("offline")) return "error"
  if (value.includes("software_update")) return "info"
  return ""
}

function chip(text, extra) {
  const element = document.createElement("span")
  element.className = `chip ${extra ?? tone(text)}`
  element.textContent = String(text ?? "").replace(/\s*\(\d+\)$/, "")
  return element
}

function uptime(seconds) {
  if (seconds == null) return ""
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
}

function renderStatus(snapshot) {
  const status = document.getElementById("status")
  const badge = document.getElementById("status-chip")
  if (snapshot.connecting) {
    status.textContent = `Connecting to mavcan:${snapshot.mavlink_url}`
    badge.textContent = "Connecting"
    badge.className = "chip warning"
  } else if (snapshot.connected) {
    status.textContent = `Node ${snapshot.node_id} on bus ${snapshot.bus} via mavcan:${snapshot.mavlink_url}`
    badge.textContent = `Connected · bus ${snapshot.bus}`
    badge.className = "chip ok"
  } else {
    status.textContent = ""
    badge.textContent = "Disconnected"
    badge.className = "chip"
  }
  if (snapshot.error) {
    status.textContent += `${status.textContent ? " — " : ""}${snapshot.error}`
    if (!snapshot.connected) badge.className = "chip error"
  }
  document.getElementById("connect-button").disabled = snapshot.connected || snapshot.connecting
  document.getElementById("disconnect-button").disabled = !snapshot.connected && !snapshot.connecting
}

function renderNodes(snapshot) {
  const body = document.getElementById("nodes")
  body.replaceChildren()
  document.getElementById("nodes-empty").hidden = snapshot.nodes.length > 0
  for (const node of snapshot.nodes) {
    const row = document.createElement("tr")
    if (node.id === state.selected) row.className = "selected"
    for (const value of [node.id, node.name, chip(node.mode), chip(node.health), uptime(node.uptime), node.software]) {
      const cell = document.createElement("td")
      cell.append(value ?? "")
      row.append(cell)
    }
    row.addEventListener("click", () => {
      state.selected = node.id
      state.detailKey = null
      renderNodes(snapshot)
      renderDetail(snapshot)
    })
    body.append(row)
  }
}

function button(label, className, action) {
  const element = document.createElement("button")
  element.type = "button"
  element.textContent = label
  element.className = className || ""
  element.addEventListener("click", action)
  return element
}

function renderDetail(snapshot) {
  const dialog = document.getElementById("node-dialog")
  const detail = document.getElementById("detail")
  const node = snapshot.nodes.find((item) => item.id === state.selected)
  if (!node) {
    if (dialog.open) dialog.close()
    return
  }
  detail.replaceChildren()
  document.getElementById("detail-title").textContent = `Node ${node.id} · ${node.name || "unknown"}`
  const title = document.createElement("div")
  title.className = "facts"
  title.append(
    chip(node.mode),
    chip(node.health),
    chip(`HW ${node.hardware ?? "?"}`, ""),
    chip(`SW ${node.software ?? "?"}`, ""),
    chip(`Up ${uptime(node.uptime)}`, ""),
  )
  const actions = document.createElement("div")
  actions.className = "actions"
  actions.append(
    button("Fetch parameters", "", () => post(`/api/nodes/${node.id}/params/fetch`)),
    button("Store", "secondary", () => post(`/api/nodes/${node.id}/opcode`, { opcode: "save" })),
    button("Erase", "secondary", () => {
      if (window.confirm(`Erase stored parameters on node ${node.id}?`)) {
        post(`/api/nodes/${node.id}/opcode`, { opcode: "erase" })
      }
    }),
    button("Restart", "danger", () => {
      if (window.confirm(`Restart node ${node.id}?`)) post(`/api/nodes/${node.id}/restart`)
    }),
  )
  const file = document.createElement("input")
  file.type = "file"
  const update = button("Update firmware", "secondary", async () => {
    if (!file.files[0]) return
    const body = new FormData()
    body.append("file", file.files[0])
    try {
      await request(`/api/nodes/${node.id}/firmware`, { method: "POST", body })
    } catch (error) {
      document.getElementById("status").textContent = error.message
    }
  })
  actions.append(file, update)
  detail.append(title, actions)
  if (!dialog.open) {
    try {
      dialog.showModal()
    } catch {
      dialog.show()
    }
  }
  const firmware = snapshot.firmware
  if (firmware && firmware.node_id === node.id) {
    const note = document.createElement("p")
    note.textContent = firmware.message
    detail.append(note)
  }
  const params = snapshot.params
  if (!params || params.node_id !== node.id) return
  if (params.error) {
    const note = document.createElement("p")
    note.textContent = params.error
    detail.append(note)
  }
  for (const param of params.items) {
    const row = document.createElement("div")
    row.className = "param"
    const name = document.createElement("span")
    name.textContent = param.name
    const kind = document.createElement("span")
    kind.className = "muted"
    kind.textContent = param.type
    const input = document.createElement("input")
    input.value = param.value == null ? "" : String(param.value)
    row.append(
      name,
      kind,
      input,
      button("Set", "secondary", () => post(`/api/nodes/${node.id}/params`, {
        name: param.name,
        type: param.type,
        value: input.value,
      })),
    )
    detail.append(row)
  }
  if (!params.done) {
    const note = document.createElement("p")
    note.className = "muted"
    note.textContent = "Fetching…"
    detail.append(note)
  }
}

function renderLogs(snapshot) {
  const levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
  document.getElementById("logs").textContent = snapshot.logs.map((entry) => (
    `#${entry.node} ${levels[entry.level] || entry.level} ${entry.source}: ${entry.text}`
  )).join("\n")
}

function renderFrames(snapshot) {
  if (document.getElementById("pause").checked) return
  const filter = document.getElementById("filter").value.trim().toLowerCase()
  const hideStatus = document.getElementById("hide-status").checked
  const lines = snapshot.frames.filter((frame) => {
    if (hideStatus && frame.type.includes("NodeStatus")) return false
    if (!filter) return true
    return `${frame.type} ${frame.src} ${frame.dst} ${frame.text}`.toLowerCase().includes(filter)
  }).slice(-80).map((frame) => `${frame.dir} #${frame.src}->${frame.dst} ${frame.type} ${frame.text}`)
  document.getElementById("frames").textContent = lines.join("\n")
}

function detailKey(snapshot) {
  const node = snapshot.nodes.find((item) => item.id === state.selected)
  const params = snapshot.params && snapshot.params.node_id === state.selected ? snapshot.params : null
  const firmware = snapshot.firmware && snapshot.firmware.node_id === state.selected ? snapshot.firmware : null
  return [
    state.selected,
    node && node.name,
    node && node.software,
    params && params.items.length,
    params && params.done,
    params && params.error,
    firmware && firmware.message,
  ].join("|")
}

function render(snapshot) {
  state.snapshot = snapshot
  renderStatus(snapshot)
  renderNodes(snapshot)
  const key = detailKey(snapshot)
  if (key !== state.detailKey) {
    state.detailKey = key
    renderDetail(snapshot)
  }
  renderLogs(snapshot)
  renderFrames(snapshot)
}

async function post(path, body) {
  try {
    await request(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    document.getElementById("status").textContent = error.message
  }
}

function connectionBody() {
  return {
    url: document.getElementById("url").value,
    node_id: Number(document.getElementById("node-id").value),
    bus: Number(document.getElementById("bus").value),
    target_system: Number(document.getElementById("target").value),
    signing_key: document.getElementById("signing").value,
  }
}

document.getElementById("connect").addEventListener("submit", (event) => {
  event.preventDefault()
  post("/api/connect", connectionBody())
})

document.getElementById("bus").addEventListener("change", () => {
  const snapshot = state.snapshot
  if (!snapshot || (!snapshot.connected && !snapshot.connecting)) return
  if (Number(document.getElementById("bus").value) === snapshot.bus) return
  post("/api/connect", connectionBody())
})

document.getElementById("disconnect-button").addEventListener("click", () => post("/api/disconnect"))
document.getElementById("close-node").addEventListener("click", () => {
  document.getElementById("node-dialog").close()
})
document.getElementById("node-dialog").addEventListener("click", (event) => {
  if (event.target.id === "node-dialog") event.currentTarget.close()
})
document.getElementById("node-dialog").addEventListener("close", (event) => {
  if (state.selected == null || event.currentTarget.open) return
  state.selected = null
  state.detailKey = null
  if (state.snapshot) renderNodes(state.snapshot)
})
document.getElementById("allocator").addEventListener("change", (event) => {
  if (state.snapshot && state.snapshot.connected) post("/api/allocator", { enabled: event.target.checked })
})
document.getElementById("filter").addEventListener("input", () => {
  if (state.snapshot) renderFrames(state.snapshot)
})
document.getElementById("hide-status").addEventListener("change", () => {
  if (state.snapshot) renderFrames(state.snapshot)
})

request("api/status").then((snapshot) => {
  if (snapshot.mavlink_url) document.getElementById("url").value = snapshot.mavlink_url
  render(snapshot)
}).catch((error) => {
  document.getElementById("status").textContent = error.message
})

const socket = new WebSocket(api("api/ws").href.replace(/^http/, "ws"))
socket.addEventListener("message", (event) => render(JSON.parse(event.data)))
