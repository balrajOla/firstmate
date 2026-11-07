module.exports = class FixtureProvider {
  id() { return 'local-grounded-fixture'; }
  async callApi(prompt) {
    return {output: prompt.includes('negative-control') ? 'Beta uses PostgreSQL.' : 'Alpha uses PostgreSQL. Beta is undecided. Source: decision-471.'};
  }
};
