## limitations or biases in your system

1. The app currently has  a single json file. It is not good for multiple people to use the app and to edit concurrently. There would need to be a more robust database to be implemented. 

2. The timing in the app right now is not really setup for a work environment where workers may need to travel. It is not really setup as an app where workers may need to be scheduled out for different people with pets in different l.ocations. 

3. The realiability of the AI is not great. The gemini calls can time out and there are fallback rules meaning the AI features are not guaranteed. 

4. No time-of-day awareness. Schedules are generated for "today" by date alone; a task's scheduled time has no relationship to the actual current clock time, so a task that has passed could appear as a pending item.

## Could your AI be misused, and how would you prevent that?
Yes, Defnitely!!
The app has no authentication or multi-tenant isolation or input sanitation. 

1. Anyone with access to the running app can see and edit all customers' and employees' data — there's no login, no per-user data separation, and customer/pet data sits in a plain, unencrypted JSON file.

2.  Task titles, pet names, and employee names are free text that gets inserted directly into the LLM prompt. Unusual or adversarial input in those fields isn't filtered before being sent to the model.

All of these issues could allow injection in to the API and have further consequences the app needs alot of work to be made viable and to make sure it is not harmful.

## What surprised you while testing your AI's reliability?

That the app actually had a fallback and that the AI was exteremely unreliable and the fallback feature was used most of the time and originally I had no idea it was happening until I started doing some manual tests and then asked questions of the AI about why there was issues of duplication and fallback. 

## describe your collaboration with AI during this project. Identify one suggestion you accepted and one you did not

On initiall collaboration I did not realize the AI decided to write everything assuming I had a Claude API key. It was an oversight on my part and I had to refactor the programming to include a gemini key because initially I didn't specify this. Oops! 

When working on the projectx I asked about  persistence of data.It gave a suggestion of using a simple JSON file which I did use although I know this is not the best for this type of app but I implemented due to time constraint since I am already late with the project due to my work schedule and health issues. 

