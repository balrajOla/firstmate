local wezterm = require 'wezterm'
local M = {}

function M.apply_to_config(config, options)
  options = options or {}
  local home = assert(options.home, 'Workbench state directory is required')
  local executable = options.executable or 'awb'
  config.keys = config.keys or {}
  local binding = {key='A', mods='CTRL|SHIFT', action=wezterm.action.SpawnCommandInNewTab{
    args={executable, '--home', home, 'open'}, domain='CurrentPaneDomain'}}
  for _, key in ipairs(config.keys) do
    if key.key == binding.key and key.mods == binding.mods then
      error('Workbench shortcut CTRL|SHIFT+A conflicts with an existing binding; choose a custom binding')
    end
  end
  table.insert(config.keys, binding)
  -- Optional because another module may already own the right status area.
  if options.status then
    wezterm.on('update-status', function(window, pane)
      local file = io.open(home .. '/terminal-status.json', 'r')
      if not file then window:set_right_status('Workbench: bridge offline'); return end
      local raw = file:read(16385); file:close()
      if not raw or #raw > 16384 then window:set_right_status('Workbench: invalid status'); return end
      local ok, state = pcall(wezterm.json_parse, raw)
      if not ok or type(state) ~= 'table' or type(state.generated_at) ~= 'number' then
        window:set_right_status('Workbench: invalid status'); return
      end
      if os.time() - state.generated_at > 15 then window:set_right_status('Workbench: status stale'); return end
      -- Display only local counts. Untrusted model text never reaches this callback.
      local text = string.format('Workbench | %d active | %d unread | %d blocked',
        tonumber(state.active) or 0, tonumber(state.unread) or 0, tonumber(state.blocked) or 0)
      window:set_right_status(text)
    end)
  end
  return config
end

return M
