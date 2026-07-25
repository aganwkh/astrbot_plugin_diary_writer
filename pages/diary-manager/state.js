const state = {
  context: null,
  overview: null,
  calendar: [],
  trends: null,
  activeTab: "overview",
  entry: null,
  notice: "",
};

export function getState() {
  return state;
}

export function updateState(values) {
  Object.assign(state, values);
  return state;
}

export function clearEntry() {
  state.entry = null;
}
