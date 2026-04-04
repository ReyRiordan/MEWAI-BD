We will flesh out and implement a prototype according to scenario #1 first before scaling to
multiple scenarios. The overall vision we are aiming for is a more immersive and user-initiated
version of the educational lab simulation that Dr. Y showed us.
User Interface
- We will start with generated images and audio that change dynamically to simulate the
scenario, with the images potentially being replaced later by video (koyal.ai).
- The simulation will begin with image(s) that correspond to the initial scenario description.
There will also be environmental audio (e.g. noisy ED), as well as a voiceover that reads out the
description/setting. The user can start talking once this introduction is complete, or we can
explicitly prompt them with a start button on the screen.
- The patient's "escalation level" will be shown as a health bar on the screen. The student's goal
is to engage in the correct behavior according to what they were taught about de-escalation, in
order to reduce this "escalation bar" all the way to 0. Once the situation is fully de-escalated,
they can move on to taking the patient's history (HPI is not obtainable until de-escalation is
complete, the patient will not cooperate).
- The user will speak to the patient with real-time voice-to-voice interaction, and verbalize
actions that they want to take (e.g. "let me give you some water"). If a student's input
corresponds to one or more preset valid actions in the scenario, the option(s) will pop up on the
screen. Once a student selects an action, the scenario will adjust (images/audio) accordingly,
and the escalation bar will be reduced. If a student's input is action-related, the patient will not
respond until the student chooses an option. If not, then the patient will just respond in real-time.
- The simulation ends when the student is successful in reducing the whole escalation bar (in
which case we automatically transition to normal history-taking), or a preset time limit is
reached.
- After the simulation ends, the student is shown a screen describing the scenario's core
teaching points, the full list of valid actions (and which ones they found/missed), and any
additional evaluations done on the interaction transcript (e.g. patient comfort, ethics standards).
Scenario File Format
- All image/audio/video assets that can be used in the simulation organized according to timing
or category (e.g. "introduction", "giving water")
- Patient case file, matching pre-existing format (speech config, demographics, chief concern,
etc)
- List of valid actions that the student can take (e.g. "give water")
- List of invalid actions that should be explicitly blocked (e.g. "forcefully restrain patient") NOTE:
undecided whether we want to have this
System Agent
- AI that acts as an intermediary between the student and patient agent, moderating the
simulation
- Provided with task instructions, scenario description, and full list of valid/invalid actions
- Reviews the transcription of each student response to determine whether to offer/block actions

Patient Agent
- AI that acts as the patient in the scenario
- Provided with task instructions, scenario description, patient case file, and current escalation
level
- Responds to whatever the student says according to patient case and current escalation (e.g.
escalation bar is low --> softer response)
Student Evaluations
- Level of success in choosing correct actions and de-escalating scenario
- Post-simulation automatic evaluations of the transcript according to rubrics (e.g. grade
according to how well student met standards for patient comfort, ethical behavior)