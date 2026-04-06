Goal: create a behavioral de-escalation simulation game for medical students to practice with that uses real-time voice-to-voice interaction with a patient and visuals that change dynamically according to student's words/actions.

User Interface
- We will start with generated images and audio that change dynamically to simulate the
scenario, with the images potentially being replaced later by video
- Before start: "start" button on top of default background scene
- After clicking "start": case's intro and goal are shown, shows start scene, "begin" button to start live voice-to-voice interaction
- The patient's "escalation level" will be shown as a health bar on the screen. The student's goal
is to engage in the correct behavior according to what they were taught about de-escalation, in
order to reduce this "escalation bar" all the way to 0 (HPI is not obtainable until de-escalation is
complete, the patient will not cooperate)
- The user will speak to the patient with real-time voice-to-voice interaction, and verbalize
actions that they want to take (e.g. "let me give you some water"). If a student's input
corresponds to one or more preset valid actions in the scenario, there will be some text that shows what action was detected and the escalation bar + scene will adjust automatically
- Simulation ends when escalation bar hits 0 (de-escalation successful), max value (unsuccessful, game over), or time limit is reached
- After the simulation ends, the student is shown a screen describing the scenario's core
teaching points, the full list of valid actions (and which ones they found/missed), and any
additional evaluations done on the interaction transcript (e.g. patient comfort, ethics standards).

Scenario File Format
- "speech" settings dictate what TTS model/voice are used for patient
- "intro" and "goal" desc used at beginning
- "background", "start", "success", and "fail" scenes
- "point_bar" that dictates settings for escalation bar
- List of valid actions that the student can take (e.g. "give water"), each with "type", "desc", "point_change", and "scene_change" attributes

System Agent
- AI that acts as an intermediary between the student and patient agent, moderating the
simulation
- Provided with task instructions, scenario context, and full list of actions
- Reviews the transcription of each student response to determine whether any action was detected

Patient Agent
- AI that acts as the patient in the scenario
- Provided with task instructions, scenario description, patient case file, transcript history, and current escalation
level
- Responds to whatever the student says according to patient case and current escalation (e.g.
escalation bar is lower --> softer response)

Student Evaluations
- Level of success in choosing correct actions and de-escalating scenario
- Post-simulation automatic evaluations of the transcript according to rubrics (e.g. grade
according to how well student met standards for patient comfort, ethical behavior)