pipeline {
    agent any

    options { timestamps() }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Image') {
        steps {
            sh '''
            docker buildx build \
            --platform linux/amd64 \
            --no-cache \
            -t danaziz/smart-parking-app \
            --push .
            '''
        }
        }
        
        
        stage('Run Container') {
            steps {
                sh '''
                docker stop app || true
                docker rm app || true
                docker run -d -p 8000:8000 --name app smart-parking-app
                '''
            }
        }
        
        
        stage('Push Image') {
            steps {
                sh '''
                docker tag smart-parking-app danaziz/smart-parking-app
                docker push danaziz/smart-parking-app
                '''
            }
        }
        
        stage('Deploy to EC2') {
            steps {
                sh '''
                ssh -o StrictHostKeyChecking=no ubuntu@3.139.64.245 << EOF
                docker pull danaziz/smart-parking-app
                
                docker stop app || true
                docker rm app || true
                
                docker run -d -p 8000:8000 \
                --name app \
                -e DATABASE_URL=postgresql://admin:admin@172.31.41.214:5432/parking \
                danaziz/smart-parking-app
                EOF
                '''
            }
        }
        stage('Setup Python') {
            steps {
                sh '''
                python3 -V
                python3 -m venv .venv
                . .venv/bin/activate
                python -m pip install --upgrade pip
                pip install -r requirements.txt
                '''
            }
        }

        stage('Run Tests') {
            steps {
                sh '''
                . .venv/bin/activate
                pytest -q
                '''
            }
        }

        stage('Run App') {
            steps {
                sh '''
                . .venv/bin/activate
                nohup python app.py > app.log 2>&1 &
                '''
            }
        }

        stage('Print URL') {
            steps {
                echo '🚀 App is running at: http://localhost:5055'
            }
        }
    }

    post {
    always {
        cleanWs()
    }

    success {
    withCredentials([string(credentialsId: 'slack-webhook', variable: 'SLACK_URL')]) {
        sh '''
        curl -X POST -H "Content-type: application/json" \
        --data '{"text":"✅ SUCCESS: Jenkins Build #'"${BUILD_NUMBER}"' 🚀\\n🔗 '"${BUILD_URL}"'\\n🌐 https://smart-parking-system-1jj9.onrender.com/"}' \
        $SLACK_URL
        '''
    }
}
    failure {
    withCredentials([string(credentialsId: 'slack-webhook', variable: 'SLACK_URL')]) {
        sh '''
        curl -X POST -H "Content-type: application/json" \
        --data '{"text":"❌ FAILURE: Jenkins Build #'"${BUILD_NUMBER}"' 🔥\\n🔗 '"${BUILD_URL}"'\\n🌐 https://smart-parking-system-1jj9.onrender.com/"}' \
        $SLACK_URL
        '''
    }
}
}
}
