from conftest import signup, login, new_lead


def test_summary_respects_rep_visibility(client):
    signup(client, 'luke')
    new_lead(client, est_value=500)
    signup(client, 'bryan')
    new_lead(client, est_value=100)
    own = client.get('/api/pipeline/summary').get_json()
    assert own['open_leads'] == 1
    assert own['open_value'] == 100
    login(client, 'luke')
    all_reps = client.get('/api/pipeline/summary').get_json()
    assert all_reps['open_leads'] == 2
    assert all_reps['open_value'] == 600
