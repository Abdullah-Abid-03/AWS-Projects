## SCALING TO MULTIPLE INSTANCES WITH APPLICATION LOAD BALACER (ALB)

### The Big Picture of Scaling
**Think of it like a restaurant 🍕**

Without scaling, you have one chef handling every order. If 100 people come in, the chef gets overwhelmed and slows down or crashes. Scaling means hiring more chefs and a manager who decides which chef takes which order — that manager is your **Load Balancer.**
________________________________________
#### Three Core Concepts
1. **AMI — Your Server Blueprint:** Instead of setting up each server from scratch, you take a "photograph" of your working server and use it to clone identical copies instantly. Every clone is already configured with Flask, Nginx, everything.

2. **Target Group — The Guest List:** A simple list that tells the Load Balancer "these are the servers available to handle requests." It also continuously checks each server's health so unhealthy ones are automatically removed from the list.

3. **Application Load Balancer — The Traffic Manager** Sits in front of everything. Users never talk to your EC2 instances directly — they only talk to the ALB. ALB then decides which instance handles each request, spreading the load evenly.
________________________________________
Now let me visualize this:

![Steps for Scaling](<Scaling Architect.png>)
 
________________________________________
In Plain Words
Your original setup had one chef in a restaurant. If 500 people showed up, that one chef would collapse. Scaling solves this in three steps:

- **Step 1 — Take a photo of your working server (AMI)**. Since your Flask server already works perfectly, you photograph its entire state (create Image of this)— OS, Nginx config, Flask app, everything. Now you can clone it instantly without re-doing any setup.
Noe from the EC2 instance launch as any instance as you want and simply select AMI from **My AMI.** which you have just cretaed image.

- **Step 2 — Create a guest list (Target Group)**. This is simply a list of "which servers are available and healthy right now." It constantly pings each server's /health endpoint. If a server dies, it gets removed from the list automatically. Include all the related servers in th etarget group.

- **Step 3 — Put a manager at the door (Load Balancer)**. Users never talk to your servers directly. They only talk to the ALB via one single DNS address. The ALB reads the Target Group guest list and decides which server handles each request — spreading the load evenly.

The key insight is: **users see one address, but three servers share the work behind the scenes**. If one crashes, the other two keep serving — that's high availability. If traffic doubles, you add more servers to the Target Group — that's horizontal scaling.

